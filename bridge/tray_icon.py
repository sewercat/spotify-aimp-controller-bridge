"""
TrayIcon — a minimal system-tray icon that shows bridge status and provides
a right-click menu for syncing playlists, toggling playback, and exiting.

Must run on the main thread on Windows (pystray requirement).
"""

import threading
from PIL import Image, ImageDraw
import pystray


# Spotify green
_SPOTIFY_GREEN = (30, 215, 96)
_BG            = (18, 18, 18)


def _make_icon_image(playing: bool = False) -> Image.Image:
    img  = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    colour = _SPOTIFY_GREEN if playing else (100, 100, 100)
    draw.ellipse([4, 4, 60, 60], fill=colour)
    # Three "sound wave" bars (simplified Spotify logo shape)
    for x, h in [(20, 28), (30, 36), (40, 22)]:
        y0 = 32 - h // 2
        draw.rectangle([x - 4, y0, x + 4, y0 + h], fill=(0, 0, 0))
    return img


class TrayIcon:
    def __init__(self, bridge):
        self._bridge = bridge
        self._icon: pystray.Icon | None = None

    # ── Public ────────────────────────────────────────────────────────────────

    def run(self):
        """Build and run the tray icon (blocking — call from main thread)."""
        menu = pystray.Menu(
            pystray.MenuItem("▶ / ⏸  Toggle playback",   self._toggle),
            pystray.MenuItem("⏭  Next track",             self._next),
            pystray.MenuItem("⏮  Previous track",         self._prev),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("🔄  Sync playlists",        self._sync_playlists),
            pystray.MenuItem("⭐  Sync liked songs",       self._sync_liked),
            pystray.MenuItem("💿  Sync saved albums",      self._sync_albums),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("❌  Exit",                   self._exit),
        )
        self._icon = pystray.Icon(
            name    = "spotify_aimp_bridge",
            icon    = _make_icon_image(playing=False),
            title   = "Spotify AIMP Bridge",
            menu    = menu,
        )
        # Keep icon image updated in a background thread
        threading.Thread(target=self._status_updater, daemon=True).start()
        self._icon.run()

    def update_icon(self, playing: bool):
        if self._icon:
            self._icon.icon  = _make_icon_image(playing)
            self._icon.title = (
                "Spotify AIMP Bridge — playing"
                if playing else
                "Spotify AIMP Bridge — paused"
            )

    # ── Menu actions ──────────────────────────────────────────────────────────

    def _toggle(self, *_):  self._bridge.spotify.toggle()
    def _next(self, *_):    self._bridge.spotify.next_track()
    def _prev(self, *_):    self._bridge.spotify.prev_track()

    def _sync_playlists(self, *_):
        def run():
            self._bridge.playlists.sync_all(
                on_progress=lambda i, total, name:
                    self._notify(f"Syncing {i}/{total}: {name}")
            )
            self._notify("✅ Playlists synced to AIMP folder")
        threading.Thread(target=run, daemon=True).start()

    def _sync_liked(self, *_):
        def run():
            self._bridge.playlists.sync_liked_songs()
            self._notify("✅ Liked songs synced")
        threading.Thread(target=run, daemon=True).start()

    def _sync_albums(self, *_):
        def run():
            self._bridge.playlists.sync_saved_albums()
            self._notify("✅ Albums synced")
        threading.Thread(target=run, daemon=True).start()

    def _exit(self, *_):
        self._bridge.stop()
        if self._icon:
            self._icon.stop()

    # ── Status updater ────────────────────────────────────────────────────────

    def _status_updater(self):
        import time
        while True:
            try:
                state = self._bridge.spotify.get_current_state()
                if state:
                    self.update_icon(state["is_playing"])
                    if self._icon:
                        self._icon.title = (
                            f"{'▶' if state['is_playing'] else '⏸'}  "
                            f"{state['artist']} — {state['title']}"
                        )
            except Exception:
                pass
            time.sleep(3)

    def _notify(self, message: str):
        if self._icon:
            try:
                self._icon.notify(message, "Spotify AIMP Bridge")
            except Exception:
                print(f"[Tray] {message}")
