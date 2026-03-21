"""
AIMP controller — uses pyaimp (pywin32-based) to talk directly to AIMP
via Windows messages. No HTTP plugin required, no CLI workarounds.
"""

import time
import os
import subprocess

try:
    import pyaimp
    PYAIMP_AVAILABLE = True
except ImportError:
    PYAIMP_AVAILABLE = False
    print("[AIMP] pyaimp not installed — run: pip install pyaimp pywin32")


class AIMPController:
    def __init__(self, config):
        self._client = None
        self._client_time = 0
        self.exe_path = config.get(
            "aimp", "executable_path",
            fallback=r"C:\Program Files (x86)\AIMP\AIMP.exe"
        )
        self._known_playlist_names: set[str] = set()

    # ── Client connection ─────────────────────────────────────────────────────

    def _get_client(self):
        """Return a live pyaimp.Client, refreshing if stale or AIMP restarted."""
        now = time.monotonic()
        if self._client and now - self._client_time < 5:
            return self._client
        try:
            self._client = pyaimp.Client()
            self._client_time = now
            return self._client
        except RuntimeError:
            self._client = None
            return None

    def is_running(self) -> bool:
        if not PYAIMP_AVAILABLE:
            return False
        return self._get_client() is not None

    def get_window_title(self) -> str:
        c = self._get_client()
        return "AIMP (pyaimp connected)" if c else "not found"

    # ── File loading ──────────────────────────────────────────────────────────

    def load_file(self, filepath: str) -> bool:
        """Open a file in AIMP, replacing whatever is currently loaded."""
        if not os.path.exists(filepath):
            print(f"[AIMP] File not found: {filepath}")
            return False

        if not PYAIMP_AVAILABLE:
            print("[AIMP] pyaimp not available — install it with: pip install pyaimp pywin32")
            return False

        c = self._get_client()
        if not c:
            print("[AIMP] AIMP not running or pyaimp can't find it")
            return False

        try:
            exe = self.exe_path
            if os.path.exists(exe):
                # /FILE opens the file and replaces the current playlist entry
                subprocess.Popen(
                    [exe, "/FILE", filepath],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                print(f"[AIMP] /FILE: {filepath}")
                return True
            # Fallback to add_to_playlist_and_play
            c.add_to_playlist_and_play(filepath)
            print(f"[AIMP] add_to_playlist_and_play: {filepath}")
            return True
        except Exception as exc:
            print(f"[AIMP] load_file failed: {exc}")
            self._client = None
            return False

    def _get_playlists(self, c) -> list:
        playlists = []
        pm_func = getattr(c, "get_playlist_manager", None)
        if pm_func and callable(pm_func):
            try:
                pm = pm_func()
                if hasattr(pm, "get_playlists"):
                    playlists = pm.get_playlists()
            except Exception:
                pass

        if not playlists:
            gp_func = getattr(c, "get_playlists", None)
            if gp_func and callable(gp_func):
                try:
                    playlists = gp_func()
                except Exception:
                    pass

        if not playlists:
            playlists = getattr(c, "playlists", [])
        return playlists or []

    def _activate_existing_playlist(self, c, playlist_name: str | None = None) -> bool:
        if not c or not playlist_name:
            return False
        try:
            for playlist in self._get_playlists(c):
                name = ""
                gn_func = getattr(playlist, "get_name", None)
                if gn_func and callable(gn_func):
                    try:
                        name = gn_func()
                    except Exception:
                        name = ""
                if not name:
                    name = getattr(playlist, "name", "")

                if name != playlist_name:
                    continue

                print(f"[AIMP] Found existing playlist tab: {playlist_name}")
                act_func = getattr(playlist, "activate", None)
                if act_func and callable(act_func):
                    act_func()
                else:
                    sap_func = getattr(c, "set_active_playlist", None)
                    if sap_func and callable(sap_func):
                        sap_func(playlist)
                self._known_playlist_names.add(playlist_name)
                return True
        except Exception as exc:
            print(f"[AIMP] Failed to check existing playlists: {exc}")
        return False

    def load_playlist(self, m3u8_path: str, playlist_name: str | None = None,
                      allow_create: bool = True) -> bool:
        """Open an M3U8 file in AIMP. If a playlist with the same name exists, switch to it."""
        if not os.path.exists(m3u8_path):
            print(f"[AIMP] Playlist not found: {m3u8_path}")
            return False
        
        c = self._get_client()
        if self._activate_existing_playlist(c, playlist_name):
            return True

        if not allow_create:
            if playlist_name:
                print(f"[AIMP] Playlist tab not found, skipping create: {playlist_name}")
            return False

        # If not found or failed, use CLI to load it (creates/switches tab)
        try:
            if os.path.exists(self.exe_path):
                subprocess.Popen(
                    [self.exe_path, m3u8_path],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                if playlist_name:
                    self._known_playlist_names.add(playlist_name)
                print(f"[AIMP] CLI Loaded playlist: {m3u8_path}")
                return True
        except Exception as exc:
            print(f"[AIMP] CLI playlist open failed: {exc}")

        if not c:
            return False
        try:
            c.add_to_active_playlist(m3u8_path)
            print(f"[AIMP] pyaimp Added to active playlist: {m3u8_path}")
            return True
        except Exception as exc:
            print(f"[AIMP] load_playlist failed: {exc}")
            return False

    def clear_active_playlist(self) -> bool:
        """Clear all tracks from the currently active playlist."""
        c = self._get_client()
        if not c:
            return False
        try:
            if hasattr(c, 'get_playlist_manager'):
                pm = c.get_playlist_manager()
                ap = pm.get_active_playlist()
                if ap:
                    ap.clear()
                    return True
            elif hasattr(c, 'get_active_playlist'):
                ap = c.get_active_playlist()
                if ap:
                    ap.clear()
                    return True
        except Exception:
            pass
        return False

    def play_wav_in_playlist(self, wav_path: str) -> bool:
        """Jump to and play a WAV that's already loaded in AIMP's playlist. Retries if AIMP is still loading."""
        if not os.path.exists(wav_path):
            return False
        c = self._get_client()
        if not c:
            return False
            
        # Retry up to 3 times with a small delay to handle AIMP loading
        for attempt in range(3):
            try:
                # Use add_to_playlist_and_play — when the file is already in the
                # playlist AIMP jumps to it rather than adding a duplicate
                c.add_to_playlist_and_play(wav_path)
                return True
            except Exception as exc:
                if attempt < 2:
                    print(f"[AIMP] Play attempt {attempt+1} failed, retrying... ({exc})")
                    time.sleep(1.0)
                else:
                    print(f"[AIMP] play_wav_in_playlist failed after retries: {exc}")
                    self._client = None
                    return False
        return False

    def seek(self, ms: int) -> bool:
        """Seek AIMP to position in milliseconds."""
        c = self._get_client()
        if not c:
            return False
        try:
            c.set_player_position(ms)
            return True
        except Exception as exc:
            print(f"[AIMP] seek failed: {exc}")
            return False

    def get_current_track_filename(self) -> str | None:
        c = self._get_client()
        if not c:
            return None
        try:
            info = c.get_current_track_info()
            filename = info.get("filename") or ""
            return filename.lower() if filename else None
        except Exception:
            return None

    def get_current_track_info(self) -> dict | None:
        c = self._get_client()
        if not c:
            return None
        try:
            return c.get_current_track_info()
        except Exception:
            return None

    def get_player_position(self) -> int | None:
        c = self._get_client()
        if not c:
            return None
        try:
            return c.get_player_position()
        except Exception:
            return None

    def get_volume(self) -> int | None:
        c = self._get_client()
        if not c:
            return None
        try:
            return c.get_volume()
        except Exception:
            return None

    def is_track_repeated_enabled(self) -> bool | None:
        c = self._get_client()
        if not c:
            return None
        try:
            return bool(c.is_track_repeated())
        except Exception:
            return None

    def is_shuffled_enabled(self) -> bool | None:
        c = self._get_client()
        if not c:
            return None
        try:
            return bool(c.is_shuffled())
        except Exception:
            return None

    def get_playback_state_name(self) -> str | None:
        c = self._get_client()
        if not c:
            return None
        try:
            raw = c.get_playback_state()
            if raw == pyaimp.PlayBackState.Playing:
                return "playing"
            if raw == pyaimp.PlayBackState.Paused:
                return "paused"
            return "stopped"
        except Exception:
            return None

    def _try_call(self, method_names: list[str], *args) -> bool:
        c = self._get_client()
        if not c:
            return False
        for name in method_names:
            fn = getattr(c, name, None)
            if fn and callable(fn):
                try:
                    fn(*args)
                    return True
                except Exception:
                    continue
        return False

    def set_volume(self, pct: int) -> bool:
        return self._try_call(["set_volume"], int(pct))

    def set_repeat_enabled(self, enabled: bool) -> bool:
        return self._try_call(
            ["set_track_repeated", "set_repeat", "repeat_track"],
            bool(enabled),
        )

    def set_shuffle_enabled(self, enabled: bool) -> bool:
        return self._try_call(
            ["set_shuffled", "set_shuffle", "shuffle"],
            bool(enabled),
        )

    # ── Playback controls ─────────────────────────────────────────────────────

    def _do(self, method_name: str):
        c = self._get_client()
        if c:
            try:
                getattr(c, method_name)()
            except Exception as exc:
                print(f"[AIMP] {method_name} failed: {exc}")
                self._client = None

    def play(self):       self._do("play")
    def pause(self):      self._do("pause")
    def next_track(self): self._do("next")
    def prev_track(self): self._do("prev")
    def stop(self):
        c = self._get_client()
        if c:
            try:
                if hasattr(c, 'stop'):
                    c.stop()
                elif hasattr(c, 'stop_playback'):
                    c.stop_playback()
            except Exception as exc:
                print(f"[AIMP] stop failed: {exc}")
                self._client = None
