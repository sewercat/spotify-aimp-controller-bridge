"""
PlaylistSyncer — syncs the current Spotify playlist to AIMP.

Each track gets a silent WAV of exact duration with embedded cover art and
full ID3 tags. The M3U8 goes to AIMP's PLS folder for auto-loading.
"""

import json
import os
import re
from typing import Callable


class PlaylistSyncer:
    def __init__(self, spotify_client, metadata_writer, config):
        self._spotify = spotify_client
        self._meta    = metadata_writer

        roaming = os.environ.get("APPDATA", "")
        self.pls_dir = (
            config.get("aimp", "playlist_dir", fallback="").strip()
            or os.path.join(roaming, "AIMP", "PLS")
        )
        os.makedirs(self.pls_dir, exist_ok=True)

        self.wav_root = os.path.join(self._meta.root, "Playlists")
        os.makedirs(self.wav_root, exist_ok=True)

        print(f"[Playlists] PLS dir:  {self.pls_dir}")
        print(f"[Playlists] WAV root: {self.wav_root}")

    # ── Public ─────────────────────────────────────────────────────────────────

    def sync_current_playlist(self,
                               on_progress: Callable[[int, int, str], None] | None = None
                               ) -> dict:
        playlist = self._get_current_playlist()
        if not playlist:
            return {}
        return self.sync_playlist(playlist, on_progress=on_progress)

    def sync_playlist_by_id(self,
                            playlist_id: str,
                            on_progress: Callable[[int, int, str], None] | None = None
                            ) -> dict:
        try:
            playlist = self._spotify.sp.playlist(playlist_id, fields="id,name,images")
        except Exception as exc:
            print(f"[Playlists] Could not load playlist {playlist_id}: {exc}")
            return {}
        return self.sync_playlist(playlist, on_progress=on_progress)

    def sync_playlist(self,
                      playlist: dict,
                      on_progress: Callable[[int, int, str], None] | None = None
                      ) -> dict:
        if not playlist or not playlist.get("id"):
            return {}

        name  = playlist.get("name", "Spotify")
        pl_id = playlist["id"]
        print(f"[Playlists] Syncing: {name}")

        tracks = self._spotify.get_playlist_tracks(pl_id)
        total  = len(tracks)
        if not total:
            print("[Playlists] No tracks found")
            return {}

        safe    = self._safe_name(name)
        wav_dir = os.path.join(self.wav_root, safe)
        os.makedirs(wav_dir, exist_ok=True)

        # Playlist-level cover for folder.jpg
        pl_cover = None
        pl_images = playlist.get("images", [])
        if pl_images:
            pl_cover = self._spotify.get_cover_art(pl_images[0].get("url"))
            if pl_cover:
                with open(os.path.join(wav_dir, "folder.jpg"), "wb") as f:
                    f.write(pl_cover)

        track_map       = {}  # lower wav path -> metadata
        track_id_to_wav = {}  # spotify track id -> wav path
        m3u_lines       = ["#EXTM3U", f"#PLAYLIST:{name}", ""]

        # Load existing state if it exists
        state_path = os.path.join(wav_dir, "playlist_state.json")
        existing_state = {}
        if os.path.exists(state_path):
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    existing_state = json.load(f)
            except Exception:
                pass

        new_state = []
        for i, track in enumerate(tracks):
            if not track:
                continue
            title       = track.get("name", "Unknown")
            artist      = ", ".join(a["name"] for a in track.get("artists", []))
            album_name  = track.get("album", {}).get("name", "")
            uri         = track.get("uri", "")
            track_id    = track.get("id", "")
            duration_ms = track.get("duration_ms", 30000)

            track_info = {
                "id": track_id,
                "uri": uri,
                "title": title,
                "artist": artist,
                "album": album_name,
                "duration_ms": duration_ms,
                "index": i + 1
            }
            new_state.append(track_info)

            wav_name = self._wav_name_for_track(i + 1, title)
            wav_path = os.path.join(wav_dir, wav_name)

            # Check if track is already in state and file exists
            needs_update = True
            if os.path.exists(wav_path):
                # Find this track in existing state
                match = next((t for t in existing_state if t.get("id") == track_id), None)
                if match and match.get("index") == i + 1:
                    needs_update = False

            if needs_update:
                # Fetch per-track cover art only if needed
                images = track.get("album", {}).get("images", [])
                cover  = None
                if images:
                    cover = self._spotify.get_cover_art(images[0]["url"])

                if os.path.exists(wav_path):
                    self._meta._tag(wav_path, title, artist, album_name, i + 1, cover)
                else:
                    self._meta.write_track(
                        wav_path, title, artist, album_name,
                        duration_ms, i + 1, cover
                    )

            track_map[wav_path.lower()] = {
                "uri": uri,
                "playlist_id": pl_id,
                "playlist_uri": f"spotify:playlist:{pl_id}",
                "playlist_name": name,
            }
            if track_id:
                track_id_to_wav[track_id] = wav_path

            m3u_lines.append(f"#EXTINF:{duration_ms // 1000},{artist} - {title}")
            m3u_lines.append(wav_path)

            if on_progress:
                on_progress(i + 1, total, title)
            if (i + 1) % 20 == 0:
                print(f"[Playlists]   {i+1}/{total} tracks processed...")

        m3u_path = os.path.join(self.pls_dir, safe + ".m3u8")
        with open(m3u_path, "w", encoding="utf-8") as f:
            f.write("\n".join(m3u_lines))

        # Save maps and state
        with open(os.path.join(wav_dir, "track_map.json"), "w", encoding="utf-8") as f:
            json.dump(track_map, f, ensure_ascii=False, indent=2)

        meta = {
            "playlist_id": pl_id,
            "playlist_name": name,
            "playlist_uri": f"spotify:playlist:{pl_id}",
            "m3u8_path": m3u_path,
            "wav_dir": wav_dir,
            "track_count": total,
        }
        with open(os.path.join(wav_dir, "playlist_meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(new_state, f, ensure_ascii=False, indent=2)

        print(f"[Playlists] Done — {total} tracks -> {m3u_path}")
        return {
            "playlist_name":   name,
            "playlist_id":     pl_id,
            "playlist_uri":    f"spotify:playlist:{pl_id}",
            "m3u8_path":       m3u_path,
            "wav_dir":         wav_dir,
            "track_map":       track_map,
            "track_id_to_wav": track_id_to_wav,
        }

    def load_cached_playlists(self) -> list[dict]:
        cached: list[dict] = []
        if not os.path.isdir(self.wav_root):
            return cached

        for entry in os.scandir(self.wav_root):
            if not entry.is_dir():
                continue

            wav_dir = entry.path
            meta_path = os.path.join(wav_dir, "playlist_meta.json")
            state_path = os.path.join(wav_dir, "playlist_state.json")
            track_map_path = os.path.join(wav_dir, "track_map.json")

            if not os.path.exists(track_map_path):
                continue

            track_map = {}
            if os.path.exists(track_map_path):
                try:
                    with open(track_map_path, "r", encoding="utf-8") as f:
                        track_map = json.load(f)
                except Exception as exc:
                    print(f"[Playlists] Cached map unreadable in {wav_dir}: {exc}")
                    track_map = {}

            meta = {}
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                except Exception:
                    meta = {}

            state = []
            if os.path.exists(state_path):
                try:
                    with open(state_path, "r", encoding="utf-8") as f:
                        state = json.load(f)
                except Exception:
                    state = []

            track_id_to_wav = {}
            for item in state:
                track_id = item.get("id")
                index = item.get("index")
                title = item.get("title", "Unknown")
                if not track_id or not index:
                    continue
                wav_name = self._wav_name_for_track(int(index), title)
                track_id_to_wav[track_id] = os.path.join(wav_dir, wav_name)

            playlist_name = meta.get("playlist_name") or entry.name
            m3u8_path = meta.get("m3u8_path") or os.path.join(
                self.pls_dir, self._safe_name(playlist_name) + ".m3u8"
            )

            cached.append({
                "playlist_name": playlist_name,
                "playlist_uri": meta.get("playlist_uri"),
                "playlist_id": meta.get("playlist_id"),
                "m3u8_path": m3u8_path,
                "wav_dir": wav_dir,
                "state": state,
                "track_map": track_map,
                "track_id_to_wav": track_id_to_wav,
            })

        return cached

    def refresh_cached_playlists(self,
                                 on_progress: Callable[[int, int, str], None] | None = None
                                 ) -> list[dict]:
        results: list[dict] = []
        for cached in self.load_cached_playlists():
            playlist_id = cached.get("playlist_id")
            if not playlist_id:
                results.append(cached)
                continue
            refreshed = self.sync_playlist_by_id(playlist_id, on_progress=on_progress)
            results.append(refreshed or cached)
        return results

    def repair_cached_playlist(self, cached: dict) -> dict:
        if not cached:
            return cached

        wav_dir = cached.get("wav_dir")
        playlist_name = cached.get("playlist_name", "Spotify")
        playlist_id = cached.get("playlist_id")
        playlist_uri = cached.get("playlist_uri") or (
            f"spotify:playlist:{playlist_id}" if playlist_id else None
        )
        state = cached.get("state") or []
        if not wav_dir or not state:
            return cached

        os.makedirs(wav_dir, exist_ok=True)
        track_map = dict(cached.get("track_map") or {})
        track_id_to_wav = {}
        m3u_lines = ["#EXTM3U", f"#PLAYLIST:{playlist_name}", ""]

        repaired = 0
        for item in state:
            index = int(item.get("index") or 0)
            title = item.get("title", "Unknown")
            artist = item.get("artist", "")
            album = item.get("album", "")
            duration_ms = int(item.get("duration_ms") or 1000)
            track_id = item.get("id")
            uri = item.get("uri", "")
            if index <= 0:
                continue

            wav_name = self._wav_name_for_track(index, title)
            wav_path = os.path.join(wav_dir, wav_name)
            if not os.path.exists(wav_path):
                self._meta.write_track(
                    wav_path, title, artist, album, max(duration_ms, 1000), index, None
                )
                repaired += 1

            if track_id:
                track_id_to_wav[track_id] = wav_path
            if uri:
                track_map[wav_path.lower()] = {
                    "uri": uri,
                    "playlist_id": playlist_id,
                    "playlist_uri": playlist_uri,
                    "playlist_name": playlist_name,
                }

            m3u_lines.append(f"#EXTINF:{duration_ms // 1000},{artist} - {title}")
            m3u_lines.append(wav_path)

        m3u8_path = cached.get("m3u8_path") or os.path.join(
            self.pls_dir, self._safe_name(playlist_name) + ".m3u8"
        )
        with open(m3u8_path, "w", encoding="utf-8") as f:
            f.write("\n".join(m3u_lines))

        with open(os.path.join(wav_dir, "track_map.json"), "w", encoding="utf-8") as f:
            json.dump(track_map, f, ensure_ascii=False, indent=2)

        cached["track_map"] = track_map
        cached["track_id_to_wav"] = track_id_to_wav
        cached["m3u8_path"] = m3u8_path

        if repaired:
            print(f"[Playlists] Repaired {repaired} missing file(s) for {playlist_name}")
        return cached

    # ── Spotify-generated playlist detection ──────────────────────────────────

    _BLOCKED_PREFIXES = ("37i9dQZF",)

    def _get_current_playlist(self) -> dict | None:
        try:
            pb = self._spotify.sp.current_playback()
            if not pb:
                return None
            ctx = pb.get("context")
            if not ctx or ctx.get("type") != "playlist":
                return None
            pl_id = ctx["uri"].split(":")[-1]
            for prefix in self._BLOCKED_PREFIXES:
                if pl_id.startswith(prefix):
                    print(f"[Playlists] Skipping Spotify-generated playlist")
                    return None
            return self._spotify.sp.playlist(pl_id, fields="id,name,images")
        except Exception as exc:
            print(f"[Playlists] Could not get current playlist: {exc}")
            return None

    @staticmethod
    def _safe_name(name: str) -> str:
        return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(". ")[:80]

    def _wav_name_for_track(self, index: int, title: str) -> str:
        return f"{index:04d}_{self._safe_name(title)}.wav"
