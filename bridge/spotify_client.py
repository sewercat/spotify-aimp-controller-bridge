"""
Spotify Web API client — handles auth, now-playing polling, and library access.
"""

import io
import time
import requests
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from PIL import Image

SCOPES = [
    "user-read-playback-state",
    "user-modify-playback-state",
    "user-read-currently-playing",
    "playlist-read-private",
    "playlist-read-collaborative",
    "user-library-read",
]


class SpotifyClient:
    def __init__(self, config):
        self.sp = spotipy.Spotify(
            auth_manager=SpotifyOAuth(
                client_id=config["spotify"]["client_id"],
                client_secret=config["spotify"]["client_secret"],
                redirect_uri=config["spotify"]["redirect_uri"],
                scope=" ".join(SCOPES),
                cache_path=".spotify_cache",
                open_browser=True,
            ),
            requests_timeout=5,
        )
        self._cover_cache: dict[str, bytes] = {}
        self._no_device_until = 0.0

    # ── Playback state ───────────────────────────────────────────────────────

    def get_current_state(self) -> dict | None:
        """Return a normalised dict of the current playback state, or None."""
        try:
            pb = self.sp.current_playback()
        except Exception as exc:
            print(f"[Spotify] get_current_state error: {exc}")
            return None

        if not pb or not pb.get("item"):
            return None

        track = pb["item"]
        images = track["album"].get("images", [])

        return {
            "track_id": track["id"],
            "title": track["name"],
            "artist": ", ".join(a["name"] for a in track["artists"]),
            "album": track["album"]["name"],
            "duration_ms": track["duration_ms"],
            "progress_ms": pb.get("progress_ms", 0),
            "is_playing": pb["is_playing"],
            "cover_url": images[0]["url"] if images else None,
            "volume": pb.get("device", {}).get("volume_percent", 100),
            "device": pb.get("device", {}).get("name", ""),
        }

    # ── Controls ─────────────────────────────────────────────────────────────

    def _is_no_device_error(self, exc: Exception) -> bool:
        text = str(exc)
        return ("NO_ACTIVE_DEVICE" in text) or ("No active device" in text)

    def _in_no_device_cooldown(self) -> bool:
        return time.monotonic() < self._no_device_until

    def _call(self, fn, *args, **kwargs) -> bool:
        if self._in_no_device_cooldown():
            return False
        try:
            fn(*args, **kwargs)
            return True
        except Exception as exc:
            if self._is_no_device_error(exc):
                # Back off a bit to avoid hammering Spotify when no active player exists.
                self._no_device_until = time.monotonic() + 8.0
                return False
            else:
                print(f"[Spotify] {fn.__name__} error: {exc}")
            return False

    def play(self):        return self._call(self.sp.start_playback)
    def pause(self):       return self._call(self.sp.pause_playback)
    def next_track(self):  return self._call(self.sp.next_track)
    def prev_track(self):  return self._call(self.sp.previous_track)
    def seek(self, ms):    return self._call(self.sp.seek_track, int(ms))
    def set_volume(self, pct): return self._call(self.sp.volume, int(pct))

    def play_uri(self, uri: str, context_uri: str | None = None):
        """Play a specific Spotify track URI, optionally within a playlist context."""
        if self._in_no_device_cooldown():
            return False
        try:
            if context_uri:
                self.sp.start_playback(context_uri=context_uri, offset={"uri": uri})
            else:
                self.sp.start_playback(uris=[uri])
            return True
        except Exception as exc:
            if self._is_no_device_error(exc):
                self._no_device_until = time.monotonic() + 8.0
                return False
            print(f"[Spotify] play_uri error: {exc}")
            return False

    def set_repeat(self, state: str):
        return self._call(self.sp.repeat, state)

    def set_shuffle(self, enabled: bool):
        return self._call(self.sp.shuffle, enabled)

    def toggle(self):
        state = self.get_current_state()
        if state:
            self.pause() if state["is_playing"] else self.play()

    # ── Library ───────────────────────────────────────────────────────────────

    def get_playlists(self) -> list[dict]:
        """Fetch all playlists for the current user (handles pagination)."""
        items, res = [], self.sp.current_user_playlists(limit=50)
        while res:
            items.extend(res["items"])
            res = self.sp.next(res) if res.get("next") else None
        return [p for p in items if p]

    def get_playlist_tracks(self, playlist_id: str) -> list[dict]:
        items = []
        try:
            res = self.sp.playlist_items(
                playlist_id,
                limit=100,
                additional_types=("track",),
            )
            while res:
                batch = [
                    (t.get("track") or t.get("item")) for t in res.get("items", [])
                    if t and (t.get("track") or t.get("item"))
                    and (t.get("track") or t.get("item", {})).get("id")
                ]
                items.extend(batch)
                res = self.sp.next(res) if res and res.get("next") else None
        except Exception as exc:
            import traceback
            print(f"[Spotify] get_playlist_tracks error: {exc}")
            traceback.print_exc()
        return items

    def get_liked_songs(self, max_tracks: int = 2000) -> list[dict]:
        items, res = [], self.sp.current_user_saved_tracks(limit=50)
        while res and len(items) < max_tracks:
            items.extend(t["track"] for t in res["items"] if t.get("track"))
            res = self.sp.next(res) if res.get("next") else None
        return items

    def get_saved_albums(self) -> list[dict]:
        items, res = [], self.sp.current_user_saved_albums(limit=50)
        while res:
            items.extend(a["album"] for a in res["items"] if a.get("album"))
            res = self.sp.next(res) if res.get("next") else None
        return items

    # ── Cover art ────────────────────────────────────────────────────────────

    def get_cover_art(self, url: str, size: tuple = (300, 300)) -> bytes | None:
        """Download cover art and return JPEG bytes (cached)."""
        if not url:
            return None
        if url in self._cover_cache:
            return self._cover_cache[url]
        try:
            r = requests.get(url, timeout=5)
            r.raise_for_status()
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            img = img.resize(size, Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=90)
            data = buf.getvalue()
            self._cover_cache[url] = data
            return data
        except Exception as exc:
            print(f"[Spotify] cover art error: {exc}")
            return None
