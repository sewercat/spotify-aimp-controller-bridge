"""
HotkeyHandler — intercepts global media keys and routes them to Spotify.

`suppress=True` means the keystrokes are consumed here and NOT passed on to AIMP
or any other app. This gives Spotify exclusive control of the media keys while the
bridge is running.

To also allow AIMP hotkeys, set suppress=False in config — media keys will then
reach both apps, but AIMP will act on its own playlist (silent file) while Spotify
gets the Spotify command, which is usually fine.
"""

import keyboard


class HotkeyHandler:
    def __init__(self, spotify_client, config):
        self._spotify = spotify_client
        self._suppress = config.getboolean("bridge", "suppress_media_keys", fallback=True)
        self._registered = False

    # ── Registration ──────────────────────────────────────────────────────────

    def register(self):
        if self._registered:
            return
        s = self._suppress

        keyboard.add_hotkey("play/pause media", self._toggle,    suppress=s)
        keyboard.add_hotkey("next track",        self._next,      suppress=s)
        keyboard.add_hotkey("previous track",    self._prev,      suppress=s)
        keyboard.add_hotkey("stop media",        self._pause,     suppress=s)

        # Extra convenience: Ctrl+Shift+Arrow for seek (doesn't conflict with anything)
        keyboard.add_hotkey("ctrl+shift+right",  lambda: self._seek_relative(+10_000))
        keyboard.add_hotkey("ctrl+shift+left",   lambda: self._seek_relative(-10_000))
        keyboard.add_hotkey("ctrl+shift+up",     lambda: self._vol(+10))
        keyboard.add_hotkey("ctrl+shift+down",   lambda: self._vol(-10))

        self._registered = True
        mode = "exclusive" if s else "shared"
        print(f"[Hotkeys] Media keys registered ({mode} mode)")

    def unregister(self):
        if self._registered:
            keyboard.unhook_all()
            self._registered = False
            print("[Hotkeys] Unregistered.")

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _toggle(self):
        self._spotify.toggle()

    def _next(self):
        self._spotify.next_track()

    def _prev(self):
        self._spotify.prev_track()

    def _pause(self):
        self._spotify.pause()

    def _seek_relative(self, delta_ms: int):
        state = self._spotify.get_current_state()
        if state:
            new_pos = max(0, state["progress_ms"] + delta_ms)
            self._spotify.seek(new_pos)

    def _vol(self, delta: int):
        state = self._spotify.get_current_state()
        if state:
            new_vol = max(0, min(100, state["volume"] + delta))
            self._spotify.set_volume(new_vol)
