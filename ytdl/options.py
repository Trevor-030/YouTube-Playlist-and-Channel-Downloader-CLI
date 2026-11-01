"""Command-line choices and the yt-dlp options built from them (quality, audio, subtitles, embed, speed limit)."""

import click
import importlib.util
import os

from yt_dlp.utils import parse_bytes

from ytdl.common import console


# Format strings: prefer pre-merged mp4 stream, then merge with ffmpeg, then any single-file fallback
# Prefer H.264 video + AAC audio inside each resolution: plays everywhere on Windows and
# merges into mp4 without codec trouble. Falls back to whatever exists (webm/AV1/Opus).
FORMAT_SORT = ["res", "vcodec:h264", "acodec:aac", "ext:mp4:m4a"]


QUALITY_OPTIONS = {
    "best":  "bv*+ba/b",
    "1080p": "bv*[height<=1080]+ba/b[height<=1080]",
    "720p":  "bv*[height<=720]+ba/b[height<=720]",
    "480p":  "bv*[height<=480]+ba/b[height<=480]",
    "360p":  "bv*[height<=360]+ba/b[height<=360]",
}


AUDIO_FORMATS = ("mp3", "m4a")


def parse_rate(ctx, param, value):
    """Click callback: '500K' / '2M' / '1.5M' -> bytes per second (None if not given)."""
    if not value:
        return None
    rate = parse_bytes(value)
    if not rate or rate < 1024:
        raise click.BadParameter("use a size like 500K or 2M  (bytes per second)")
    return rate


def can_embed_thumbnail(audio=None) -> bool:
    """Cover art in mp4/m4a needs the 'mutagen' package (part of yt-dlp[default]); mp3 only needs ffmpeg."""
    return audio == "mp3" or importlib.util.find_spec("mutagen") is not None


def warn_embed(embed: bool, audio=None):
    if embed and not can_embed_thumbnail(audio):
        console.print("[yellow]⚠ Cover art skipped: the 'mutagen' package is missing "
                      "(chapters and tags still embed). Fix: pip install mutagen[/yellow]\n")


def parse_langs(value):
    """'en, fr' -> ['en', 'fr'];  None/'' -> None."""
    langs = [l.strip() for l in (value or "").split(",") if l.strip()]
    return langs or None


def build_ydl_opts(output_dir: str, quality: str, resume: bool,
                   playlist_start: int, playlist_end: int, id_in_name: bool = True,
                   audio: str = None, subs: list = None, embed: bool = False,
                   rate_limit: int = None) -> dict:
    fmt = QUALITY_OPTIONS.get(quality, QUALITY_OPTIONS["best"])

    # Windows-safe output template (avoids illegal filename chars)
    # The video id in the name ties every leftover file to its video (and survives title changes)
    name = "%(playlist_index)03d - %(title).100s" + (" [%(id)s]" if id_in_name else "") + ".%(ext)s"
    # Audio-only goes to its own folder, so it never mixes with (or hides behind) video downloads
    folder = "%(playlist_title).80s" + (" (audio)" if audio else "")
    outtmpl = os.path.join(output_dir.replace("%", "%%"), folder, name)

    opts = {
        "format": fmt,
        "format_sort": FORMAT_SORT,
        "outtmpl": outtmpl,
        "continuedl": resume,
        "concurrent_fragment_downloads": 4,
        "retries": 10,                 # network drops are what leave .part files behind
        "fragment_retries": 10,
        "ignoreerrors": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [],
        # Merge into mp4 (works well on Windows without extra codecs)
        "merge_output_format": "mp4",
        # Restrict filenames to ASCII-safe chars for Windows compatibility
        "restrictfilenames": False,
        "windowsfilenames": True,
    }

    if audio:
        for key in ("format_sort", "merge_output_format"):
            opts.pop(key, None)
        opts["format"] = "bestaudio[ext=m4a]/bestaudio/best" if audio == "m4a" else "bestaudio/best"
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": audio,
                                   "preferredquality": "192"}]

    if subs:
        # Real captions first; YouTube's auto-generated ones only where no real captions exist.
        # Saved beside the video as  <name>.<lang>.srt
        opts.update({
            "writesubtitles": True,
            "writeautomaticsub": subs != ["all"],     # 'all' would pull dozens of auto translations
            "subtitleslangs": subs,
            "subtitlesformat": "srt/vtt/best",
            "sleep_interval_subtitles": 1,            # YouTube rate-limits caption requests
        })
        opts.setdefault("postprocessors", []).append(
            {"key": "FFmpegSubtitlesConvertor", "format": "srt", "when": "before_dl"})

    if rate_limit:
        # The limit applies per connection, so a single connection keeps the cap honest
        opts["ratelimit"] = rate_limit
        opts["concurrent_fragment_downloads"] = 1

    if embed:
        # Chapters + tags (title, channel, date…) go in via ffmpeg; cover art on top when possible.
        # Order matters: metadata first, thumbnail last.
        pps = opts.setdefault("postprocessors", [])
        pps.append({"key": "FFmpegMetadata", "add_chapters": True, "add_metadata": True})
        if can_embed_thumbnail(audio):
            opts["writethumbnail"] = True
            pps.append({"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"})
            pps.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})   # deletes the loose image

    if playlist_start > 1:
        opts["playliststart"] = playlist_start
    if playlist_end > 0:
        opts["playlistend"] = playlist_end

    return opts
