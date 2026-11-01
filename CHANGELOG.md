# Changelog

All notable changes to this project. Newest first.

## [1.1.0] - 2026-11-01

### Fixed
- Videos left as separate audio and video files (`name.f137.mp4` + `name.f140.m4a`) after a failed ffmpeg merge.
  Merge errors are now shown instead of hidden, and the tool merges leftovers itself after every download.
- Broken or missing `history.json` could crash a download. History problems are now ignored.
- Playlist mode now retries dropped connections (10 retries), as channel mode already did.
- Very long titles no longer risk the Windows path limit (names are cut at 100 characters).

### Added
- `yt repair [FOLDER]` merges leftover video/audio files. It handles emoji and very long file names and
  tells you when the other half is only a half-finished `.part` download.
- Automatic retry passes for unfinished `.part` downloads (`--retry-passes`, default 3) in playlist and channel mode.
- Download archive in playlist mode (`downloaded.txt`, `--no-archive` to turn off). Re-runs only fetch what is missing.
- New playlist files are named `001 - Title [video id].mp4`. Folders from older versions keep their old naming.
- `yt add-ids URL` renames files in old folders to include the video id. Dry run by default, `--apply` to rename.
- `--audio mp3|m4a` for audio-only downloads, saved in a separate `(audio)` folder.
- `--subs en` saves subtitles as `.srt` (real captions first, then auto-generated).
- `--embed` puts cover art, chapters and tags (title, channel, date) inside each file.
- `yt batch urls.txt` downloads a list of playlists, channels and videos with no prompts, with a summary table.
- `--rate-limit 2M` caps the download speed.
- `--notify` shows a desktop notification and plays a sound when a job finishes; `yt notify-test` checks it works.
- `yt update` upgrades yt-dlp. A warning appears when yt-dlp is more than 60 days old.
- Warning for finished files that are shorter than YouTube says (possibly cut off).
- Clean message on Ctrl+C in playlist mode.
- `pyproject.toml`: install with `pipx install .` to get the `yt` command anywhere.
- Test suite (`pytest`, no internet needed) and a GitHub Actions workflow that runs it on Windows and Linux,
  and every Monday to catch breakage from new yt-dlp releases.

### Changed
- Video format choice now prefers H.264 video and AAC audio within each resolution (falls back to anything else).
  Plays everywhere on Windows and merges into mp4 without codec trouble. Files can be a little larger than AV1.
- The code is split into the `ytdl/` package. `yt_downloader.py` stays the entry point, so `yt.bat` and `yt` keep working.

## [1.0.0]

- First version: download playlists and whole channels with yt-dlp, rich progress, config, history, `setup.bat`.
