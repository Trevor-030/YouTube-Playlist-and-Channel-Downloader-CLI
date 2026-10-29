# 🎬 YouTube Playlist & Channel Downloader — Windows Edition

A CLI tool to download YouTube playlists and entire channels with quality selection, resume support, and history tracking.

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

### Optional — install as a command (any OS)

```
pipx install .
```

This gives you `yt` everywhere without `setup.bat`. Needs Python 3.10+ and ffmpeg on PATH.

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

:: Re-run the same command any time: finished videos are skipped, broken ones are retried
:: Unfinished (.part) downloads are retried automatically, 3 extra passes by default
yt download "URL" --retry-passes 5
yt download "URL" --retry-passes 0
yt download "URL" --no-archive

:: Download EVERY video from a channel (@handle, /channel/UC…, /c/… all work)
yt channel "https://www.youtube.com/@ChannelName"
yt channel "URL" -q 720p -o "D:\Videos"

:: Re-run the same command later to grab only NEW uploads
yt channel "URL"

:: Extras: include Shorts and live streams, latest 50 only, or only since a date
yt channel "URL" --tabs all
yt channel "URL" --limit 50
yt channel "URL" --since 20240101

:: Pasting a channel URL into `download` also switches to channel mode
yt download "https://www.youtube.com/@ChannelName"

:: Preview playlist (or channel) contents without downloading
yt info "URL"

:: Fix videos that came out as separate audio + video files
yt repair
yt repair "D:\Videos"

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
    ├── 001 - First Video Title [dQw4w9WgXcQ].mp4
    ├── 002 - Second Video Title [9bZkp7q19f0].mp4
    ├── downloaded.txt
    └── ...
```

The `[id]` ties every file (and any leftover part) to its video. `downloaded.txt` lists finished
videos so re-runs only fetch what is missing — delete a line from it to force a re-download.
Playlist folders made by older versions (names without `[id]`) keep their old naming so nothing downloads twice.

---

Channel downloads go to `<output>\Channel Name\2024-03-15 - Video Title [id].mp4`
(with `--tabs all`, each tab gets its own subfolder). Finished videos are logged in
`<output>\Channel Name\downloaded.txt`, which is what makes re-runs skip what you already have.

---

## 🎛️ Quality Options

Within each resolution the tool prefers H.264 video + AAC audio. It plays everywhere on Windows and
merges cleanly into mp4. If a video has no such streams, it falls back to the next best (webm / AV1 / Opus).

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
| "may be cut off" warning after a run | That file is shorter than YouTube says. Delete it and its line in `downloaded.txt`, then re-run |
| "yt-dlp is N days old" warning | Run `pip install -U "yt-dlp[default]"`. Old versions cause 403s and failed downloads |
| Separate audio + video files (`name.f137.mp4` + `name.f140.m4a`) | The ffmpeg merge failed. The tool now retries the merge after every download; to fix old folders run `yt repair` |
| Channel run stops / gets slow | Ctrl+C and re-run the same command; it resumes. Update yt-dlp if YouTube changed |
| Members-only / private videos | Skipped and listed as errors in the summary |
