"""
config.py - loads config.ini and prompts for first-run Spotify credentials.
"""

import configparser
import os
import sys

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.ini")
AIMP_ROAMING = os.path.join(os.environ.get("APPDATA", ""), "AIMP")

DEFAULTS = {
    "spotify": {
        "client_id": "PASTE_YOUR_CLIENT_ID_HERE",
        "client_secret": "PASTE_YOUR_CLIENT_SECRET_HERE",
        "redirect_uri": "http://127.0.0.1:8888/callback",
    },
    "aimp": {
        "executable_path": r"C:\Program Files (x86)\AIMP\AIMP.exe",
        "playlist_dir": os.path.join(AIMP_ROAMING, "PLS"),
        "remote_port": "38475",
    },
    "bridge": {
        "poll_interval": "1.0",
        "suppress_media_keys": "true",
        "show_sync_window": "true",
        "temp_dir": os.path.join(AIMP_ROAMING, "SpotifyBridge"),
        "sync_on_startup": "true",
    },
}


def _write_config(cfg: configparser.ConfigParser, path: str):
    with open(path, "w", encoding="utf-8") as f:
        cfg.write(f)


def _needs_spotify_credentials(cfg: configparser.ConfigParser) -> bool:
    client_id = cfg.get("spotify", "client_id", fallback="").strip()
    client_secret = cfg.get("spotify", "client_secret", fallback="").strip()
    return (
        not client_id
        or not client_secret
        or client_id.startswith("PASTE_")
        or client_secret.startswith("PASTE_")
    )


def _prompt_spotify_credentials(cfg: configparser.ConfigParser, path: str) -> bool:
    if not sys.stdin or not sys.stdin.isatty():
        return False

    print("\n[Config] Spotify credentials required for first run.")
    print("         Leave either field blank to cancel.\n")

    client_id = input("Spotify Client ID: ").strip()
    if not client_id:
        return False
    client_secret = input("Spotify Client Secret: ").strip()
    if not client_secret:
        return False

    cfg["spotify"]["client_id"] = client_id
    cfg["spotify"]["client_secret"] = client_secret
    _write_config(cfg, path)
    print(f"\n[Config] Saved credentials to:\n  {path}\n")
    return True


def load_config(path: str = CONFIG_PATH) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    for section, values in DEFAULTS.items():
        cfg[section] = values

    if not os.path.exists(path):
        _write_config(cfg, path)
        print(f"\n[Config] Created default config at:\n  {path}")

    cfg.read(path, encoding="utf-8")

    if _needs_spotify_credentials(cfg):
        if not _prompt_spotify_credentials(cfg, path):
            print("[Config] ERROR: Spotify credentials are missing.")
            print(f"         Edit {path} and add your credentials.")
            sys.exit(1)
        cfg.read(path, encoding="utf-8")

    return cfg
