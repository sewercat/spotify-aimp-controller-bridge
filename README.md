# Spotify AIMP Bridge

A Windows background application that creates a deep, two-way bridge between Spotify and AIMP.

Unlike simple "Now Playing" widgets, this bridge generates local silent audio files for your playlists. This allows you to browse and control Spotify directly from the AIMP interface—clicking a track in AIMP instantly plays it in Spotify.

---

## Features

### Two-Way Control

* Control Spotify via Media Keys
* Control Spotify via AIMP UI

  * Clicking "Next" or selecting a track in AIMP triggers the action in Spotify

### Full Playlist Syncing

* Exports Spotify playlists to AIMP as `.m3u8` files containing local WAVs
* AIMP sees a real playlist of files
* Supports:

  * Album Art
  * Artist
  * Title
  * Track Numbers

### State Synchronization

* **Seek Bar:** AIMP's progress bar syncs to Spotify (updates every 2s)
* **Volume & Shuffle:** Changes in AIMP are mirrored to Spotify

### Native Integration

* Uses `pyaimp` (Windows Messages) for low-latency control
* No HTTP plugins required

### Background Operation

* Runs in the system tray with status indicators

---

## Requirements

* Windows 10 / 11
* Python 3.11+
* AIMP 34-bit (v4 or v5)
* Spotify Premium (required for playback control API)

## Required Plugin (Important)

This project depends on the AIMP Control Plugin:

https://github.com/a0ivanov/aimp-control-plugin

Notes:
* The plugin only works with 32-bit AIMP
* Ensure you install the 32-bit version of AIMP, not 64-bit
* The plugin exposes a local HTTP server used by the bridge
* The bridge connects to: http://127.0.0.1:<PORT>
* <PORT> is determined by the plugin configuration
* Make sure the port in your bridge config matches the plugin’s port

---

## Installation

### 1. Clone the Repository


```bash 
git clone https://github.com/sewercat/spotify-aimp-bridge.git
cd spotify-aimp-bridge
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

**Required packages:**

* pyaimp
* pywin32
* spotipy
* Pillow
* pystray
* keyboard
* mutagen
* requests

---

### 3. Configure Spotify API

1. Go to the Spotify Developer Dashboard
2. Create a new application
3. Copy:

   * Client ID
   * Client Secret
4. Add Redirect URI:
   http://127.0.0.1:8888/callback

---

### 4. First Run

```
python main.py
```

On first launch:

* You’ll be prompted to enter credentials
* They will be saved in `config.ini`

---

## Usage

### How It Works

**1. The WAVs**

* Silent WAV files are generated in:
  C:\Users\USER-NAME\AppData\Roaming\AIMP\SpotifyBridge

**2. AIMP Loads Them**

* AIMP treats them as real files
* Displays metadata (title, artist, album art)

**3. The Hook**

* Clicking a file in AIMP:

  * Changes current filename
  * Bridge detects change
  * Spotify plays the real track

---

### Controls

| Action     | Method                    |
| ---------- | ------------------------- |
| Play/Pause | Media Keys / AIMP button  |
| Next/Prev  | Media Keys / AIMP buttons |
| Seek       | Drag AIMP seek bar        |
| Volume     | Adjust in AIMP            |

---

## Building the EXE (Optional)

Run the PowerShell script:
.\build_exe.ps1

Output:
dist\SpotifyAIMPBridge.exe

---

## Configuration (config.ini)

Generated automatically on first run.

```
[spotify]
client_id     = YOUR_CLIENT_ID
client_secret = YOUR_CLIENT_SECRET
redirect_uri  = http://127.0.0.1:8888/callback

[aimp]
executable_path = C:\Program Files (x86)\AIMP\AIMP.exe
playlist_dir    =

[bridge]
poll_interval       = 1.0
suppress_media_keys = true
show_sync_window    = true
sync_on_startup     = true
```

---

## Troubleshooting

### AIMP not responding

* Ensure both AIMP and the bridge run with the same permissions
* If AIMP is Administrator → run bridge as Administrator
* Verify `pyaimp` and `pywin32` installation

---

### Progress bar not updating

* Sync happens every 2 seconds
* Delay is expected behavior

---

### Playlists empty in AIMP

Playlist location:
%AppData%\AIMP\PLS

Fix:

1. Open AIMP
2. Right-click Playlist area → "Load Playlist"
3. Navigate to folder

Or enable:
sync_on_startup = true

---

## Technical Details

### Silent WAV Format

* 8000Hz
* Mono
* 8-bit

Optimized for:

* Minimal file size
* Fast generation
* ID3 tag compatibility

---

### State Machine

* Prevents feedback loops:

  * AIMP → Spotify → AIMP → Spotify

---

###  Hook Watcher

* Background thread monitors AIMP’s current filename
* Detects user interaction instantly

---

## Concept

This project works by turning AIMP into a **native UI layer for Spotify**, using local files as a bridge between:

* Desktop media player behavior
* Streaming API control

---

## License

MIT License (or your choice)
