"""
Spotify AIMP Bridge - main entry point.
"""

import json
import os
import shutil
import subprocess
import threading
import time

from config import load_config
from spotify_client import SpotifyClient
from aimp_controller import AIMPController
from aimp_hook import AIMPHook
from metadata_writer import MetadataWriter
from hotkey_handler import HotkeyHandler
from playlist_syncer import PlaylistSyncer
from sync_progress_window import SyncProgressWindow
from tray_icon import TrayIcon


class SpotifyAIMPBridge:
    def __init__(self):
        print("[Bridge] Initialising...")
        self.config    = load_config()
        self.spotify   = SpotifyClient(self.config)
        self.aimp      = AIMPController(self.config)
        self.hook      = AIMPHook(self.spotify, self.aimp)
        self.metadata  = MetadataWriter(self.config)
        self.playlists = PlaylistSyncer(self.spotify, self.metadata, self.config)
        self.hotkeys   = HotkeyHandler(self.spotify, self.config)
        self.progress_window = SyncProgressWindow(
            enabled=self.config.getboolean("bridge", "show_sync_window", fallback=True)
        )
        self.tray      = TrayIcon(self)

        self._running = False
        self._last_track_id = None
        self._last_playlist_id = None
        self._last_aimp_state = None
        self._playlist_loaded = False
        self._track_id_to_wav: dict[str, str] = {}
        self._track_map: dict[str, dict | str] = {}
        self._cached_playlists: dict[str, dict] = {}
        self._current_m3u8: str | None = None
        self._sync_ready = threading.Event()

        self._aimp_initiated_track_id: str | None = None
        self._aimp_initiated_playlist_id: str | None = None
        self._aimp_click_deadline = 0.0
        self._aimp_click_serial = 0
        self._last_realign_attempt = 0.0
        self._last_spotify_present = False
        self._last_aimp_running = False
        self._last_presence_sync = 0.0
        self._last_reconcile_to_spotify_attempt = 0.0

    def start(self):
        print("[Bridge] Starting...")
        self._running = True

        print("[Bridge] Authenticating with Spotify...")
        _ = self.spotify.get_current_state()
        print("[Bridge] Spotify OK")

        self.hotkeys.register()
        self.progress_window.start()
        self.hook.set_on_aimp_click(self._on_aimp_click)
        threading.Thread(target=self._initial_sync,       name="InitialSync",   daemon=True).start()
        threading.Thread(target=self._sync_loop,          name="SyncLoop",      daemon=True).start()
        threading.Thread(target=self._state_sync,         name="StateSync",     daemon=True).start()
        threading.Thread(target=self._hook_watcher,       name="HookWatcher",   daemon=True).start()
        threading.Thread(target=self._aimp_close_watcher, name="CloseWatcher",  daemon=True).start()
        threading.Thread(target=self._progress_sync,      name="ProgressSync",  daemon=True).start()
        threading.Thread(target=self._status_printer,     name="StatusPrinter", daemon=True).start()

        print("[Bridge] Running -- tray icon active, media keys -> Spotify")
        print("[Bridge] Right-click the tray icon to sync playlists or exit.\n")
        self.tray.run()

    def stop(self):
        print("[Bridge] Stopping...")
        self._running = False
        self.progress_window.close()
        self.hook.uninstall()
        self.hotkeys.unregister()

    def _on_aimp_click(self, track_id: str, playlist_uri: str | None):
        print(f"[Bridge] AIMP click registered for track {track_id[:8]}...")
        self._aimp_click_serial += 1
        self._aimp_initiated_track_id = track_id
        self._aimp_initiated_playlist_id = (
            playlist_uri.split(":")[-1] if playlist_uri and ":" in playlist_uri else None
        )
        self._aimp_click_deadline = time.monotonic() + 5.0

    def _clear_pending_aimp_click(self):
        self._aimp_initiated_track_id = None
        self._aimp_initiated_playlist_id = None
        self._aimp_click_deadline = 0.0

    def _pending_aimp_click_active(self) -> bool:
        if time.monotonic() <= self._aimp_click_deadline:
            return True
        self._clear_pending_aimp_click()
        return False

    def _expected_wav_for_track(self, track_id: str | None) -> str | None:
        if not track_id:
            return None
        return self._track_id_to_wav.get(track_id)

    def _aimp_matches_track(self, track_id: str | None) -> bool:
        expected = self._expected_wav_for_track(track_id)
        if not expected:
            return False
        current = self.aimp.get_current_track_filename()
        if not current:
            return False
        return current == expected.lower()

    def _maybe_realign_aimp(self, track_id: str | None) -> bool:
        expected = self._expected_wav_for_track(track_id)
        if not expected or not os.path.exists(expected) or not self.aimp.is_running():
            return False
        if self._aimp_matches_track(track_id):
            return False

        now = time.monotonic()
        if now - self._last_realign_attempt < 1.0:
            return False
        self._last_realign_attempt = now

        ok = self.aimp.play_wav_in_playlist(expected)
        if ok:
            print("=== AIMP REALIGN =================================")
            print(f"  File   : {os.path.basename(expected)}")
            print("  Result : OK")
            self.hook.notify_track_loaded(expected)
        return ok

    def _reconcile_to_aimp(self, spotify_state: dict) -> bool:
        track_id = spotify_state.get("track_id")
        wav_path = self._expected_wav_for_track(track_id)
        if not wav_path or not os.path.exists(wav_path) or not self.aimp.is_running():
            return False

        print("=== PRESENCE SYNC =================================")
        print("  Source : Spotify -> AIMP")

        ok = self.aimp.play_wav_in_playlist(wav_path)
        if not ok:
            print("  Result : Failed to load current Spotify track in AIMP")
            return False

        progress_ms = spotify_state.get("progress_ms")
        if progress_ms is not None:
            self.aimp.seek(progress_ms)

        volume = spotify_state.get("volume")
        if volume is not None:
            self.aimp.set_volume(volume)

        repeat_state = spotify_state.get("repeat_state")
        if repeat_state:
            self.aimp.set_repeat_enabled(repeat_state == "track")

        shuffle_state = spotify_state.get("shuffle_state")
        if shuffle_state is not None:
            self.aimp.set_shuffle_enabled(bool(shuffle_state))

        if spotify_state.get("is_playing"):
            self.aimp.play()
        else:
            self.aimp.pause()

        self.hook.notify_track_loaded(wav_path)
        self._last_track_id = track_id
        print("  Result : AIMP matched Spotify state")
        return True

    def _reconcile_to_spotify(self) -> bool:
        if not self.aimp.is_running():
            return False

        filename = self.aimp.get_current_track_filename()
        if not filename:
            return False

        entry = self._track_map.get(filename)
        if isinstance(entry, str):
            entry = {"uri": entry, "playlist_uri": None}
        if not isinstance(entry, dict):
            return False

        uri = entry.get("uri")
        if not uri:
            return False

        print("=== PRESENCE SYNC =================================")
        print("  Source : AIMP -> Spotify")

        self.spotify.play_uri(uri, entry.get("playlist_uri"))
        time.sleep(0.35)

        aimp_pos = self.aimp.get_player_position()
        if aimp_pos is not None:
            self.spotify.seek(aimp_pos)

        volume = self.aimp.get_volume()
        if volume is not None:
            self.spotify.set_volume(volume)

        repeat_enabled = self.aimp.is_track_repeated_enabled()
        if repeat_enabled is not None:
            self.spotify.set_repeat("track" if repeat_enabled else "off")

        shuffle_enabled = self.aimp.is_shuffled_enabled()
        if shuffle_enabled is not None:
            self.spotify.set_shuffle(shuffle_enabled)

        aimp_state = self.aimp.get_playback_state_name()
        if aimp_state == "paused":
            self.spotify.pause()
        elif aimp_state == "playing":
            self.spotify.play()

        self._on_aimp_click(uri.split(":")[-1], entry.get("playlist_uri"))
        print("  Result : Spotify matched AIMP state")
        return True

    def _handle_presence_changes(self, spotify_state: dict | None):
        now = time.monotonic()
        spotify_present = spotify_state is not None
        aimp_running = self.aimp.is_running()

        if now - self._last_presence_sync < 1.0:
            self._last_spotify_present = spotify_present
            self._last_aimp_running = aimp_running
            return

        if spotify_present and not self._last_spotify_present and aimp_running:
            if self.aimp.get_current_track_filename():
                if self._reconcile_to_spotify():
                    self._last_presence_sync = now

        if (not spotify_present
                and aimp_running
                and self.aimp.get_current_track_filename()
                and now - self._last_reconcile_to_spotify_attempt > 3.0):
            self._last_reconcile_to_spotify_attempt = now
            self._reconcile_to_spotify()

        if aimp_running and not self._last_aimp_running and spotify_present:
            if self._reconcile_to_aimp(spotify_state):
                self._last_presence_sync = now

        self._last_spotify_present = spotify_present
        self._last_aimp_running = aimp_running

    def _ensure_aimp_open(self):
        if self.aimp.is_running():
            print("[Bridge] AIMP already running")
            self._sync_on_app_launch()
            return
        exe = self.config.get(
            "aimp", "executable_path",
            fallback=r"C:\Program Files (x86)\AIMP\AIMP.exe",
        )
        if os.path.exists(exe):
            print(f"[Bridge] Launching AIMP: {exe}")
            subprocess.Popen([exe], creationflags=subprocess.CREATE_NO_WINDOW)
            for _ in range(20):
                time.sleep(0.5)
                if self.aimp.is_running():
                    print("[Bridge] AIMP is up")
                    self._sync_on_app_launch()
                    return
        print("[Bridge] AIMP not found or did not start")

    def _sync_on_app_launch(self):
        now = time.monotonic()
        if now - self._last_presence_sync < 1.0:
            return

        self._restore_cached_playlists_to_aimp()

        spotify_state = self.spotify.get_current_state()
        if spotify_state and self.aimp.is_running():
            if self._reconcile_to_aimp(spotify_state):
                self._last_presence_sync = now
                self._last_spotify_present = True
                self._last_aimp_running = True
                return

        if self.aimp.is_running() and self.aimp.get_current_track_filename():
            if self._reconcile_to_spotify():
                self._last_presence_sync = now
                self._last_aimp_running = True
                self._last_spotify_present = self.spotify.get_current_state() is not None

    def _restore_cached_playlists_to_aimp(self):
        if not self.aimp.is_running():
            return

        restored_any = False
        for result in self._cached_playlists.values():
            result = self.playlists.repair_cached_playlist(result)
            m3u8 = result.get("m3u8_path")
            name = result.get("playlist_name")
            if not m3u8 or not os.path.exists(m3u8):
                continue
            if self.aimp.load_playlist(m3u8, name, allow_create=True):
                restored_any = True

        # Fallback: if bridge metadata is missing, restore every saved AIMP playlist file.
        if restored_any:
            return

        pls_dir = getattr(self.playlists, "pls_dir", None)
        if not pls_dir or not os.path.isdir(pls_dir):
            return

        for entry in sorted(os.scandir(pls_dir), key=lambda item: item.name.lower()):
            if not entry.is_file() or not entry.name.lower().endswith(".m3u8"):
                continue
            playlist_name = os.path.splitext(entry.name)[0]
            self.aimp.load_playlist(entry.path, playlist_name, allow_create=True)

    def _initial_sync(self):
        time.sleep(2)
        self._report_progress(0, 100, "Restoring cached playlists...")

        restored = self.playlists.load_cached_playlists()
        for result in restored:
            result = self.playlists.repair_cached_playlist(result)
            self._store_playlist_result(result)

        result = self.playlists.sync_current_playlist(on_progress=self._on_sync_progress)
        if result:
            self._store_playlist_result(result)
            self._last_playlist_id = result.get("playlist_uri", "").split(":")[-1] or self._get_current_playlist_id()
            self._activate_playlist_result(result, allow_create=False)
            print(f"[Bridge] Playlist ready: {result['playlist_name']}")
        else:
            self._last_playlist_id = self._get_current_playlist_id()
            cached = self._cached_playlists.get(self._last_playlist_id) if self._last_playlist_id else None
            if cached:
                cached = self.playlists.repair_cached_playlist(cached)
                self._store_playlist_result(cached)
                self._activate_playlist_result(cached, allow_create=True)
                print(f"[Bridge] Playlist ready from cache: {cached['playlist_name']}")
            else:
                print("[Bridge] No syncable playlist context")

        self._report_progress(100, 100, "Sync complete!")
        time.sleep(1)
        self._report_progress(None, None, "")

        self._ensure_aimp_open()
        self._sync_ready.set()

    def _on_sync_progress(self, current, total, title):
        percent = int((current / total) * 100) if total else 0
        self._report_progress(percent, 100, f"Syncing: {title}")

    def _report_progress(self, current, total, message):
        try:
            with open("bridge_progress.json", "w", encoding="utf-8") as f:
                json.dump({
                    "current": current,
                    "total": total,
                    "message": message,
                    "timestamp": time.time(),
                }, f)
        except Exception:
            pass
        self.progress_window.update(current, total, message)

    def _store_playlist_result(self, result: dict):
        if not result:
            return
        playlist_uri = result.get("playlist_uri")
        playlist_id = result.get("playlist_id") or (
            playlist_uri.split(":")[-1] if playlist_uri else None
        )
        if playlist_id:
            self._cached_playlists[playlist_id] = result

        self._playlist_loaded = True
        self._track_map.update(result.get("track_map", {}))
        self._track_id_to_wav.update(result.get("track_id_to_wav", {}))
        self.hook.set_track_map(self._track_map, playlist_uri)

    def _activate_playlist_result(self, result: dict, allow_create: bool = True):
        if not result:
            return

        m3u8 = result.get("m3u8_path")
        name = result.get("playlist_name")
        self._current_m3u8 = m3u8

        if not m3u8 or not self.aimp.is_running():
            return

        if self.aimp.load_playlist(m3u8, name, allow_create=allow_create):
            self.hook.suppress(3.0)

    def _sync_playlist_id(self, playlist_id: str) -> dict:
        self._report_progress(0, 100, "Playlist changed, re-syncing...")
        result = self.playlists.sync_playlist_by_id(playlist_id, on_progress=self._on_sync_progress)
        if not result:
            cached = self._cached_playlists.get(playlist_id)
            if cached:
                print(f"[Bridge] Using cached playlist for {playlist_id[:8]}...")
                result = self.playlists.repair_cached_playlist(cached)
        if result:
            self._store_playlist_result(result)
        self._report_progress(100, 100, "Sync complete!")
        time.sleep(1)
        self._report_progress(None, None, "")
        return result

    def _hook_watcher(self):
        while self._running:
            if not self.hook._hooked and self.aimp.is_running():
                self.hook.install()
            elif self.hook._hooked and not self.aimp.is_running():
                self.hook._hooked = False
            time.sleep(2)

    def _sync_loop(self):
        poll = float(self.config.get("bridge", "poll_interval", fallback="1.0"))
        while self._running:
            try:
                self._tick()
            except Exception as exc:
                print(f"[Bridge] Sync error: {exc}")
            time.sleep(poll)

    def _tick(self):
        if not self._sync_ready.wait(timeout=0):
            return

        pending_aimp_click = self._pending_aimp_click_active()
        state = self.spotify.get_current_state()
        self._handle_presence_changes(state)
        if not state:
            return

        current_pl_id = self._get_current_playlist_id()
        if current_pl_id and current_pl_id != self._last_playlist_id:
            print("")
            print("=== PLAYLIST CHANGE =============================")
            print(f"  Old ID : {(self._last_playlist_id or 'none')[:8]}...")
            print(f"  New ID : {current_pl_id[:8]}...")

            aimp_initiated_change = (
                pending_aimp_click and current_pl_id == self._aimp_initiated_playlist_id
            )
            was_playing = state.get("is_playing", False)

            if was_playing and not aimp_initiated_change:
                self.spotify.pause()
                self.hook.suppress(5.0)
                self.aimp.stop()
                print("  Action : Paused Spotify and stopped AIMP for sync")
            elif aimp_initiated_change:
                print("  Action : AIMP selected another playlist; keeping playback context")

            self._last_playlist_id = current_pl_id
            result = self._sync_playlist_id(current_pl_id)
            if result:
                self._activate_playlist_result(result, allow_create=not aimp_initiated_change)

            if was_playing and not aimp_initiated_change:
                self.spotify.play()
                print("  Action : Resumed Spotify after sync")
                new_state = self.spotify.get_current_state()
                if new_state:
                    self._last_track_id = new_state["track_id"]
                    self.hook.notify_track_loaded()

        pending_target = self._aimp_initiated_track_id if pending_aimp_click else None
        if pending_target and state["track_id"] != pending_target:
            print("")
            print("=== AIMP CLICK PENDING ===========================")
            print(f"  Waiting : {pending_target[:8]}...")
            print(f"  Current : {state['track_id'][:8]}...")
            return

        if not pending_target:
            self._maybe_realign_aimp(state["track_id"])

        if state["track_id"] == self._last_track_id:
            return

        print("")
        print("=== SPOTIFY =====================================")
        print(f"  Track  : {state['title']}")
        print(f"  Artist : {state['artist']}")
        print(f"  Album  : {state['album']}")
        print(f"  Status : {'Playing' if state['is_playing'] else 'Paused'}")
        print(f"  Pos    : {state['progress_ms']//1000}s / {state['duration_ms']//1000}s")
        print(f"  Vol    : {state['volume']}%")
        print(f"  ID     : {state['track_id']}")

        tid = state["track_id"]
        if tid == self._aimp_initiated_track_id:
            print("")
            print("=== AIMP CLICK ==================================")
            print(f"  Track  : {state['title']}")
            print(f"  Artist : {state['artist']}")
            print("  Source : AIMP click -> Spotify playing")
            print("  Result : OK, no redirect needed")
            self._last_track_id = tid
            if not self._aimp_matches_track(tid):
                self._maybe_realign_aimp(tid)
            self.hook.notify_track_loaded(self._track_id_to_wav.get(tid))
            self._clear_pending_aimp_click()
            return

        wav_path = self._track_id_to_wav.get(tid)
        if self._playlist_loaded and wav_path and os.path.exists(wav_path):
            ok = self.aimp.play_wav_in_playlist(wav_path)
            print("=== AIMP ========================================")
            print("  Action : Jump to playlist track")
            print(f"  File   : {os.path.basename(wav_path)}")
            print(f"  Result : {'OK' if ok else 'FAILED'}")
            print(f"  Map    : {len(self._track_id_to_wav)} tracks loaded")
            if ok:
                self.hook.notify_track_loaded(wav_path)
        else:
            print("=== AIMP ========================================")
            print("  Action : No jump - track not in playlist map")
            print(f"  Map    : {len(self._track_id_to_wav)} tracks, ID={state['track_id'][:8]}...")

        self._last_track_id = tid

    def _state_sync(self):
        import pyaimp as _pyaimp
        while self._running:
            try:
                c = self.aimp._get_client()
                if c:
                    raw = c.get_playback_state()
                    if raw == _pyaimp.PlayBackState.Playing:
                        aimp_state = "playing"
                    elif raw == _pyaimp.PlayBackState.Paused:
                        aimp_state = "paused"
                    else:
                        aimp_state = "stopped"

                    if aimp_state != self._last_aimp_state:
                        in_suppress = time.monotonic() < self.hook._suppress_until
                        sp_state = self.spotify.get_current_state()
                        sp_playing = sp_state["is_playing"] if sp_state else None
                        print("")
                        print("=== STATE SYNC ==================================")
                        print(f"  AIMP    : {self._last_aimp_state} -> {aimp_state}")
                        print(f"  Spotify : {'Playing' if sp_playing else 'Paused' if sp_playing is False else 'Unknown'}")
                        if in_suppress:
                            print("  Action  : Suppressed (playlist loading)")
                        elif aimp_state == "paused" and self._last_aimp_state == "playing":
                            if sp_playing:
                                self.spotify.pause()
                                print("  Action  : Paused Spotify")
                        elif aimp_state == "playing" and self._last_aimp_state in ("paused", "stopped"):
                            if sp_playing is False:
                                self.spotify.play()
                                print("  Action  : Resumed Spotify")
                        self._last_aimp_state = aimp_state
            except Exception as exc:
                print(f"[Sync] Error: {exc}")
            time.sleep(0.5)

    def _progress_sync(self):
        last_synced_track = None
        while self._running:
            try:
                state = self.spotify.get_current_state()
                if state and state["is_playing"]:
                    c = self.aimp._get_client()
                    if c:
                        aimp_pos = c.get_player_position()
                        sp_pos = state["progress_ms"]
                        drift = abs(aimp_pos - sp_pos)
                        if drift > 3000 or state["track_id"] != last_synced_track:
                            self.aimp.seek(sp_pos)
                            last_synced_track = state["track_id"]
            except Exception:
                pass
            time.sleep(2)

    def _status_printer(self):
        import pyaimp as _pyaimp
        while self._running:
            try:
                sp = self.spotify.get_current_state()
                c = self.aimp._get_client()

                sp_title = f"{sp['artist']} - {sp['title']}" if sp else "Nothing playing"
                sp_status = ("Playing" if sp and sp["is_playing"] else "Paused") if sp else "Stopped"
                sp_pos = f"{sp['progress_ms']//1000}s/{sp['duration_ms']//1000}s" if sp else "--"

                aimp_title = "?"
                aimp_status = "Stopped"
                aimp_pos = "--"
                if c:
                    try:
                        info = c.get_current_track_info()
                        raw = c.get_playback_state()
                        pos = c.get_player_position()
                        dur = info.get("duration", 0)
                        aimp_title = f"{info.get('artist', '?')} - {info.get('title', '?')}"
                        if raw == _pyaimp.PlayBackState.Playing:
                            aimp_status = "Playing"
                        elif raw == _pyaimp.PlayBackState.Paused:
                            aimp_status = "Paused"
                        aimp_pos = f"{pos//1000}s/{dur//1000}s"
                    except Exception:
                        aimp_title = "Error reading AIMP"

                is_sync = False
                if sp and aimp_title not in {"?", "Error reading AIMP"}:
                    s_title = sp["title"].lower()
                    a_title = aimp_title.lower()
                    if s_title[:15] in a_title or a_title in s_title:
                        is_sync = True

                if time.monotonic() < self.hook._suppress_until:
                    match = "SYNCING..."
                else:
                    match = "IN SYNC" if is_sync else "MISMATCH"

                print("")
                print(f"SPOTIFY [{sp_status}] {sp_title[:55]}")
                print(f"  {sp_pos}")
                print(f"AIMP    [{aimp_status}] {aimp_title[:55]}")
                print(f"  {aimp_pos}")
                print(f"STATUS   {match}")
            except Exception as exc:
                print(f"[Status] Error: {exc}")
            time.sleep(5)

    def _aimp_close_watcher(self):
        was_running = False
        while self._running:
            is_running = self.aimp.is_running()
            if was_running and not is_running:
                print("[Bridge] AIMP closed - cleaning up WAV cache...")
                self._cleanup_cache()
                self._playlist_loaded = False
                self._current_m3u8 = None
            was_running = is_running
            time.sleep(3)

    def _cleanup_cache(self):
        root = self.metadata.root
        try:
            for fname in os.listdir(root):
                fpath = os.path.join(root, fname)
                if os.path.isfile(fpath):
                    os.remove(fpath)
                    print(f"[Bridge] Removed: {fpath}")
        except Exception as exc:
            print(f"[Bridge] Cleanup error: {exc}")

    def _get_current_playlist_id(self) -> str | None:
        try:
            pb = self.spotify.sp.current_playback()
            if pb:
                ctx = pb.get("context")
                if ctx and ctx.get("type") == "playlist":
                    return ctx["uri"].split(":")[-1]
        except Exception:
            pass
        return None


if __name__ == "__main__":
    bridge = SpotifyAIMPBridge()
    bridge.start()
