import os
"""
AIMPHook - polls AIMP state to mirror controls to Spotify.
"""

import threading
import time


class AIMPHook:
    def __init__(self, spotify_client, aimp_controller):
        self._spotify  = spotify_client
        self._aimp     = aimp_controller
        self._running  = False
        self._thread   = None
        self._hooked   = False

        self._last_position  = None
        self._last_volume    = -1
        self._last_repeat    = None
        self._last_shuffle   = None
        self._last_filename  = None

        self._suppress_until = 0.0
        self._skip_cooldown  = 0.0

        self._track_map: dict[str, dict | str] = {}
        self._playlist_uri: str | None = None
        self._expected_filename: str | None = None
        self._on_aimp_click = None  # callback(track_id, playlist_uri)
        self._click_generation = 0
        self._dispatch_delay = 0.45

    def install(self) -> bool:
        if self._hooked:
            return True
        if not self._aimp._get_client():
            return False
        self._hooked  = True
        self._running = True
        self._thread  = threading.Thread(
            target=self._poll_loop, name="AIMPPoller", daemon=True
        )
        self._thread.start()
        print("[Hook] AIMP poller started")
        return True

    def uninstall(self):
        self._running = False
        self._hooked  = False

    def suppress(self, duration: float = 1.5):
        self._suppress_until = time.monotonic() + duration
        self._last_position  = None
        self._last_filename  = None

    def notify_track_loaded(self, expected_filename: str | None = None):
        self._expected_filename = (expected_filename or "").lower() or None
        self.suppress(1.5)

    def set_on_aimp_click(self, callback):
        self._on_aimp_click = callback

    def set_track_map(self, track_map: dict, playlist_uri: str | None = None):
        self._track_map    = track_map
        self._playlist_uri = playlist_uri
        print(f"[Hook] Track map loaded ({len(track_map)} entries)")

    def _poll_loop(self):
        while self._running:
            try:
                self._tick()
            except Exception as exc:
                print(f"[Hook] Poll error: {exc}")
                time.sleep(2)
            time.sleep(0.35)

    def _tick(self):
        c = self._aimp._get_client()
        if not c:
            return
        now = time.monotonic()

        try:
            vol = c.get_volume()
            if self._last_volume == -1:
                self._last_volume = vol
            elif vol != self._last_volume:
                print(f"[Hook] Volume: {self._last_volume}% -> {vol}%")
                self._spotify.set_volume(vol)
                self._last_volume = vol
        except Exception:
            pass

        try:
            repeat = c.is_track_repeated()
            if self._last_repeat is None:
                self._last_repeat = repeat
            elif repeat != self._last_repeat:
                print(f"[Hook] Repeat -> {'track' if repeat else 'off'}")
                self._spotify.set_repeat("track" if repeat else "off")
                self._last_repeat = repeat
        except Exception:
            pass

        try:
            shuffle = c.is_shuffled()
            if self._last_shuffle is None:
                self._last_shuffle = shuffle
            elif shuffle != self._last_shuffle:
                print(f"[Hook] Shuffle -> {shuffle}")
                self._spotify.set_shuffle(shuffle)
                self._last_shuffle = shuffle
        except Exception:
            pass

        try:
            info = c.get_current_track_info()
            filename = (info.get("filename") or "").lower()
            in_suppress = now < self._suppress_until
            expected_match = (
                in_suppress
                and self._expected_filename
                and filename == self._expected_filename
            )

            if (self._last_filename is not None
                    and filename
                    and filename != self._last_filename
                    and now > self._skip_cooldown
                    and not expected_match):
                entry = self._track_map.get(filename)
                if isinstance(entry, str):
                    entry = {"uri": entry, "playlist_uri": self._playlist_uri}

                uri = entry.get("uri") if isinstance(entry, dict) else None
                playlist_uri = (
                    entry.get("playlist_uri")
                    if isinstance(entry, dict) else self._playlist_uri
                )
                if uri and uri.startswith("spotify:track:"):
                    track_id = uri.split(":")[-1]
                    print("")
                    print("=== AIMP TRACK CLICK ============================")
                    print(f"  File     : {os.path.basename(filename)}")
                    print(f"  Title    : {info.get('title', '?')}")
                    print(f"  URI      : {uri[:40]}...")
                    if self._on_aimp_click:
                        self._on_aimp_click(track_id, playlist_uri)
                    self._click_generation += 1
                    generation = self._click_generation
                    threading.Thread(
                        target=self._dispatch_click_playback,
                        args=(generation, uri, playlist_uri, filename),
                        daemon=True,
                    ).start()
                    self._suppress_until = now + 1.5
                    self._skip_cooldown  = now + 1.5
                    self._expected_filename = filename

            self._last_filename = filename
            if in_suppress:
                try:
                    self._last_position = c.get_player_position()
                except Exception:
                    pass
                return
        except Exception:
            pass

    def _dispatch_click_playback(self, generation: int, uri: str,
                                 playlist_uri: str | None, filename: str):
        time.sleep(self._dispatch_delay)
        if generation != self._click_generation:
            return
        self._expected_filename = filename
        self._spotify.play_uri(uri, playlist_uri)

        try:
            pos  = c.get_player_position()
            prev = self._last_position
            if prev is None:
                self._last_position = pos
                return

            if (prev > 10000
                    and pos < 300
                    and now > self._skip_cooldown
                    and now > self._suppress_until):
                print("")
                print("=== SKIP DETECTED ===============================")
                print(f"  AIMP pos : {prev}ms -> {pos}ms")
                print("  Action   : Spotify next_track")
                threading.Thread(target=self._spotify.next_track, daemon=True).start()
                self._suppress_until = now + 2.0
                self._skip_cooldown  = now + 2.0
                self._last_position  = pos
                return

            self._last_position = pos
        except Exception:
            pass
