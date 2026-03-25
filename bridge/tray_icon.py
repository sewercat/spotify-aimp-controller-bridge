"""
System tray icon and quick controls.
"""

import threading
import time
import os
from PIL import Image
import pystray


TRAY_ICON_PATH = os.path.join(os.path.dirname(__file__), "icon.png")
def load_icon():
    return Image.open(TRAY_ICON_PATH)

class TrayIcon:
    def __init__(self, bridge):
        self._bridge = bridge
        self._icon: pystray.Icon | None = None

    def run(self):
        menu = pystray.Menu(
            pystray.MenuItem("Show Window", self._show_window),
            pystray.MenuItem("Hide Window", self._hide_window),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Play/Pause", self._toggle),
            pystray.MenuItem("Next Track", self._next),
            pystray.MenuItem("Previous Track", self._prev),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Sync Current Playlist", self._sync_current_playlist),
            pystray.MenuItem("Clear Cache/Data", self._clear_cache_data),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", self._exit),
        )
        self._icon = pystray.Icon(
            name="spotify_aimp_bridge",
            icon=load_icon(),
            title="Spotify AIMP Bridge",
            menu=menu,
        )
        threading.Thread(target=self._status_updater, daemon=True).start()
        self._icon.run()

    def update_icon(self, playing: bool):
        if self._icon:
            self._icon.title = "Spotify AIMP Bridge - Playing" if playing else "Spotify AIMP Bridge - Paused"
        

    def _show_window(self, *_):
        self._bridge.window.show()

    def _hide_window(self, *_):
        self._bridge.window.hide()

    def _toggle(self, *_):
        self._bridge.spotify.toggle()

    def _next(self, *_):
        self._bridge.spotify.next_track()

    def _prev(self, *_):
        self._bridge.spotify.prev_track()

    def _sync_current_playlist(self, *_):
        self._bridge.manual_sync_current_playlist()

    def _clear_cache_data(self, *_):
        self._bridge.clear_all_cache_data()

    def _exit(self, *_):
        self._bridge.stop()
        if self._icon:
            self._icon.stop()

    def _status_updater(self):
        while True:
            try:
                state = self._bridge.spotify.get_current_state()
                if state:
                    self.update_icon(state["is_playing"])
                    if self._icon:
                        self._icon.title = f"{'Playing' if state['is_playing'] else 'Paused'}: {state['artist']} - {state['title']}"
            except Exception:
                pass
            time.sleep(3)
