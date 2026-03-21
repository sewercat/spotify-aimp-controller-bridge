"""
MetadataWriter — writes tagged silent WAV files for AIMP display.
Each playlist track gets a WAV of exact duration with embedded cover art.
"""

import os
import tempfile
import wave

from mutagen.id3 import APIC, TALB, TIT2, TPE1, TRCK
from mutagen.wave import WAVE

SAMPLE_RATE  = 8000
CHANNELS     = 1
SAMPLE_WIDTH = 1


class MetadataWriter:
    def __init__(self, config):
        roaming  = os.environ.get("APPDATA", tempfile.gettempdir())
        default  = os.path.join(roaming, "AIMP", "SpotifyBridge")
        self._root = (
            config.get("bridge", "temp_dir", fallback="").strip() or default
        )
        os.makedirs(self._root, exist_ok=True)

    @property
    def root(self) -> str:
        return self._root

    # ── Playlist track WAV — exact duration + embedded cover ─────────────────

    def write_track(self, dest_path: str, title: str, artist: str,
                    album: str, duration_ms: int, track_num: int = 0,
                    cover_bytes: bytes | None = None) -> str:
        """Create a silent WAV of exact duration with full tags + embedded cover."""
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)

        # Rewrite missing or clearly oversized legacy files using the compact format.
        if (not os.path.exists(dest_path)
                or self._should_rewrite_compact(dest_path, max(duration_ms, 1000))):
            self._write_silence(dest_path, max(duration_ms, 1000))

        # Always re-tag (cover art might have been fetched after first write)
        self._tag(dest_path, title, artist, album, track_num, cover_bytes)
        return dest_path

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _tag(path: str, title: str, artist: str, album: str,
             track_num: int = 0, cover_bytes: bytes | None = None):
        audio = WAVE(path)
        if audio.tags is None:
            audio.add_tags()
        tags = audio.tags
        tags.add(TIT2(encoding=3, text=title))
        tags.add(TPE1(encoding=3, text=artist))
        tags.add(TALB(encoding=3, text=album))
        if track_num:
            tags.add(TRCK(encoding=3, text=str(track_num)))
        if cover_bytes:
            tags.add(APIC(
                encoding=3,
                mime="image/jpeg",
                type=3,
                desc="Cover",
                data=cover_bytes,
            ))
        audio.save()

    @staticmethod
    def _write_silence(path: str, duration_ms: int):
        """Write a silent WAV of exact duration."""
        num_samples  = int(duration_ms / 1000 * SAMPLE_RATE) + 1
        chunk_frames = 4096
        chunk_bytes  = bytes([128] * (chunk_frames * CHANNELS * SAMPLE_WIDTH))
        with wave.open(path, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(SAMPLE_WIDTH)
            wf.setframerate(SAMPLE_RATE)
            written = 0
            while written < num_samples:
                wf.writeframes(chunk_bytes)
                written += chunk_frames

    @staticmethod
    def _should_rewrite_compact(path: str, duration_ms: int) -> bool:
        try:
            current_size = os.path.getsize(path)
        except OSError:
            return True

        expected_size = int(duration_ms / 1000 * SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH) + 4096
        return current_size > expected_size * 2
