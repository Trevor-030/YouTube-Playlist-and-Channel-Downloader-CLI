# 🎬 YouTube Playlist Downloader — Windows Edition

A CLI tool to download YouTube playlists with quality selection, resume support, and history tracking.

---

## ⚡ Quick Start (Windows)

### Step 1 — Prerequisites

**Python 3.10+**
Download from https://python.org/downloads  
✅ Check **"Add Python to PATH"** during install.

**ffmpeg** (needed to merge video + audio)
Open PowerShell or CMD and run one of:
```
winget install Gyan.FFmpeg
```
or
```
choco install ffmpeg
```
or download manually from https://ffmpeg.org/download.html and add to PATH.

---

### Step 2 — Setup

Double-click **`setup.bat`** (or run it in CMD):
```
setup.bat
```
This installs Python packages and creates a `yt.bat` shortcut so you can just type `yt` anywhere in that folder.

---

### Step 3 — Use it

```bat
:: Download a full playlist (best quality)
yt download "https://youtube.com/playlist?list=YOUR_ID"

:: Choose quality
yt download "URL" --quality 1080p
yt download "URL" --quality 720p

:: Custom output folder
yt download "URL" --output "D:\Videos\Music"

:: Both at once
yt download "URL" -q 720p -o "C:\Users\You\Videos"

:: Download only videos 3 to 10
yt download "URL" --start 3 --end 10

:: Resume if it was interrupted (on by default)
yt download "URL" --resume

:: Preview playlist contents without downloading
yt info "URL"

:: View download history
yt history

:: Clear history
yt clear-history
```

---

## ⚙️ Config — Set your defaults

So you don't have to type `-q` and `-o` every time:

```bat
:: Set default output folder and quality
yt config --output "D:\Videos" --quality 720p

:: View current config
yt config
```

Config is saved to: `%APPDATA%\YTDownloader\config.json`

---

## 📁 Output Structure

```
D:\Videos\
└── Playlist Name\
    ├── 001 - First Video Title.mp4
    ├── 002 - Second Video Title.mp4
    └── ...
```

---

## 🎛️ Quality Options

| Option  | Description               |
|---------|---------------------------|
| `best`  | Highest available (default)|
| `1080p` | Up to Full HD             |
| `720p`  | Up to HD                  |
| `480p`  | Standard definition       |
| `360p`  | Low quality / small files |

---

## 📜 History

History is saved automatically after every download.  
Location: `%APPDATA%\YTDownloader\history.json`

View it anytime:
```bat
yt history
yt history --last 50
```

---

## 🔧 Troubleshooting

| Problem | Fix |
|---|---|
| `python not recognized` | Re-install Python, tick "Add to PATH" |
| `ffmpeg not found` | Run `winget install Gyan.FFmpeg`, restart CMD |
| `ERROR: Unable to download` | Update yt-dlp: `pip install -U yt-dlp` |
| Filenames look weird | Already handled — Windows-safe filenames enabled |
| Video has no sound | ffmpeg is missing or not on PATH |
