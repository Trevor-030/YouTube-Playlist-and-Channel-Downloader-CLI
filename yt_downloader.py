#!/usr/bin/env python3
"""
YouTube Playlist & Channel Downloader CLI  —  Windows Edition
-----------------------------------------------------
Requirements:
    pip install yt-dlp rich click
    ffmpeg must be installed and on PATH (see README)
"""

import os
import re
import importlib.util
import json
import sys
import shutil
import subprocess
import time
import click
from datetime import datetime
from pathlib import Path

try:
    import yt_dlp
    from yt_dlp.utils import sanitize_filename, parse_bytes
    from yt_dlp.version import __version__ as YTDLP_VERSION
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
    from rich.table import Table
    from rich.panel import Panel
    from rich.markup import escape
    from rich import box
except ImportError:
    print("Missing dependencies. Run:  pip install yt-dlp rich click")
    sys.exit(1)

console = Console()

# ── ffmpeg check ───────────────────────────────────────────────────────────────

def check_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None

def require_ffmpeg():
    if not check_ffmpeg():
        console.print(Panel(
            "[bold red]ffmpeg not found on PATH![/bold red]\n\n"
            "ffmpeg is required to merge video and audio into a single .mp4 file.\n"
            "Without it, you get separate [yellow].f136.mp4[/yellow] and [yellow].f251.webm[/yellow] files.\n\n"
            "[bold]Fix — run ONE of these in PowerShell / CMD (then restart terminal):[/bold]\n\n"
            "  [cyan]winget install Gyan.FFmpeg[/cyan]   (recommended)\n"
            "  [cyan]choco install ffmpeg[/cyan]          (if you have Chocolatey)\n\n"
            "Download manually: https://ffmpeg.org/download.html\n"
            "Then add the [bold]bin[/bold] folder to your system PATH.",
            title="⚠  Missing ffmpeg",
            border_style="red",
        ))
        raise SystemExit(1)


# ── Repair: merge leftover video/audio parts ───────────────────────────────────
# yt-dlp downloads video and audio as separate files (name.f137.mp4 + name.f140.m4a)
# and merges them with ffmpeg. If the merge fails, those parts are left behind.

# name.f137.mp4 / name.f251.webm / name.f251-drc.webm / name.f140-1.m4a
_PART_RE = re.compile(r"^(?P<base>.+)\.f(?P<itag>\d+)(?:-[A-Za-z0-9]+)*\.(?P<ext>[A-Za-z0-9]+)$")
_AUDIO_EXTS  = {"m4a", "mp3", "opus", "aac", "ogg", "oga", "wav", "flac"}
_AUDIO_ITAGS = {"139", "140", "141", "249", "250", "251", "256", "258", "325", "327", "328", "338", "599", "600"}


def find_split_files(root: Path) -> dict:
    """Group leftover  <name>.fNNN.<ext>  files by <name>."""
    groups = {}
    for p in Path(root).rglob("*"):
        m = _PART_RE.match(p.name)
        if m and p.is_file():
            groups.setdefault(p.parent / m["base"], []).append(p)
    return groups


def find_incomplete(root: Path) -> list:
    """Half-downloaded files (.part / .ytdl) — these need the download re-run, not a merge."""
    return [p for p in Path(root).rglob("*") if p.is_file() and p.suffix in (".part", ".ytdl")]


def _guess_kinds(path: Path) -> set:
    """Fallback when ffprobe can't tell: judge by extension / YouTube format id."""
    m = _PART_RE.match(path.name)
    if m and (m["ext"].lower() in _AUDIO_EXTS or m["itag"] in _AUDIO_ITAGS):
        return {"audio"}
    return {"video"}


def _probe_kinds(path: Path) -> set:
    probe = shutil.which("ffprobe")
    if not probe:
        return set()
    r = subprocess.run(
        [probe, "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", path.name],
        capture_output=True, text=True, errors="replace", cwd=path.parent)
    return set(r.stdout.split())


def _ffmpeg_merge(video: Path, audio: Path, out: Path, audio_args: list) -> tuple:
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", video.name, "-i", audio.name,
           "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", *audio_args,
           "-movflags", "+faststart", out.name]
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", cwd=out.parent)
    ok = r.returncode == 0 and out.exists() and out.stat().st_size > 0
    return ok, r.stderr.strip()


def merge_group(base: Path, parts: list) -> tuple:
    """Merge one video's parts. Returns (status, detail); status is merged/exists/failed/skipped.

    ffmpeg/ffprobe only ever see short ASCII temp names (emoji, accents and very long
    titles in file names break them on Windows); the real names are restored afterwards.
    """
    folder = base.parent
    target = folder / f"{base.name}.mp4"
    if target.exists():
        return "exists", ""

    moved = []          # (temp path, original path)
    out = folder / "_rp_out.mp4"
    status, detail = "skipped", ""
    try:
        for i, p in enumerate(parts):
            t = folder / f"_rp{i}{p.suffix}"
            p.rename(t)
            moved.append((t, p))

        kinds = {t: (_probe_kinds(t) or _guess_kinds(orig)) for t, orig in moved}
        detail = ", ".join(f"{orig.name[-28:]} → {'+'.join(sorted(kinds[t]))}" for t, orig in moved)
        video = next((t for t in kinds if "video" in kinds[t] and "audio" not in kinds[t]), None)
        audio = next((t for t in kinds if "audio" in kinds[t] and "video" not in kinds[t]), None)

        if video and audio:
            ok, err = _ffmpeg_merge(video, audio, out, ["-c:a", "copy"])
            if not ok:   # e.g. Opus audio can't go into mp4 — re-encode just the audio
                ok, err = _ffmpeg_merge(video, audio, out, ["-c:a", "aac", "-b:a", "192k"])
            if ok:
                out.replace(target)
                for t, _ in moved:
                    t.unlink(missing_ok=True)
                moved = []
                status = "merged"
            else:
                status, detail = "failed", err[-200:]
    finally:
        out.unlink(missing_ok=True)
        for t, orig in moved:           # not merged → put the original names back
            if t.exists():
                t.rename(orig)
    return status, detail


def repair_folder(root: Path) -> tuple:
    """Find and merge split video/audio files under root. Returns (found, fixed, incomplete)."""
    groups = find_split_files(root)
    incomplete = find_incomplete(root)
    if not groups and not incomplete:
        return 0, 0, 0

    fixed = 0
    if groups:
        console.print(f"\n[yellow]🔧 {len(groups)} video(s) came out as separate audio + video files — merging…[/yellow]")
    for base, parts in sorted(groups.items()):
        name = escape(base.name[:60])
        status, detail = merge_group(base, parts)
        if status == "merged":
            fixed += 1
            console.print(f"  [green]✓[/green] {name}")
        elif status == "exists":
            console.print(f"  [yellow]•[/yellow] {name} — merged .mp4 already exists; leftover parts can be deleted")
        elif status == "failed":
            console.print(f"  [red]✗[/red] {name} — ffmpeg could not merge it")
            console.print(f"    [dim]{escape(detail)}[/dim]")
        else:
            waiting = [q for q in incomplete if q.parent == base.parent and q.name.startswith(base.name + ".f")]
            if waiting:
                console.print(f"  [yellow]⏳ {name} — one half is still a half-finished .part download; "
                              f"re-run the same download command to finish it, then it merges[/yellow]")
            else:
                console.print(f"  [dim]– {name} — no video + audio pair found[/dim]")
            console.print(f"    [dim]{escape(detail)}[/dim]")

    if incomplete:
        console.print(f"\n[yellow]⚠ {len(incomplete)} incomplete download(s) (.part/.ytdl) — "
                      f"re-run the same download command to finish them:[/yellow]")
        for p in incomplete[:5]:
            console.print(f"  [dim]{escape(p.name[:80])}[/dim]")
        if len(incomplete) > 5:
            console.print(f"  [dim]… and {len(incomplete) - 5} more[/dim]")
    return len(groups), fixed, len(incomplete)


# ── Config & history stored in %APPDATA%\YTDownloader ──────────────────────────
APP_DIR      = Path(os.environ.get("APPDATA", Path.home())) / "YTDownloader"
HISTORY_FILE = APP_DIR / "history.json"
CONFIG_FILE  = APP_DIR / "config.json"
APP_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_OUTPUT = str(Path.home() / "Videos" / "YT Downloads")

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


# ── Config helpers ─────────────────────────────────────────────────────────────

def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"default_output": DEFAULT_OUTPUT, "default_quality": "best"}


def save_config(cfg: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


# ── History helpers ────────────────────────────────────────────────────────────

def load_history() -> list:
    """History is a convenience: a missing or damaged file must never break a download."""
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save_history(entry: dict):
    history = load_history()
    history.append(entry)
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


# ── Windows path sanitiser ─────────────────────────────────────────────────────

def safe_win_path(p: str) -> str:
    """Expand env vars like %USERPROFILE% and resolve the path."""
    return str(Path(os.path.expandvars(p)).resolve())


# ── yt-dlp helpers ─────────────────────────────────────────────────────────────

def fetch_playlist_info(url: str) -> dict | None:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "ignoreerrors": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            return ydl.extract_info(url, download=False)
        except Exception as e:
            console.print(f"[red]Error fetching info: {e}[/red]")
            return None


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m|\[[0-9;]*m")


def clean_msg(msg: str) -> str:
    return _ANSI_RE.sub("", msg).strip()


def warn_if_old_ytdlp(max_age_days: int = 60):
    """YouTube changes often; an old yt-dlp is the usual cause of 403s and broken downloads."""
    try:
        released = datetime.strptime(".".join(YTDLP_VERSION.split(".")[:3]), "%Y.%m.%d")
    except ValueError:
        return
    age = (datetime.now() - released).days
    if age > max_age_days:
        console.print(f"[yellow]⚠ yt-dlp {YTDLP_VERSION} is {age} days old. Update:[/yellow] "
                      f"[cyan]pip install -U \"yt-dlp\\[default]\"[/cyan]\n")


def media_duration(path: Path):
    """Length in seconds via ffprobe, or None. Probes a short ASCII hard link (emoji names break ffprobe on Windows)."""
    probe = shutil.which("ffprobe")
    if not probe or not path.is_file():
        return None
    tmp = path.parent / f"_rp_probe{path.suffix}"
    try:
        tmp.unlink(missing_ok=True)
        os.link(path, tmp)
        r = subprocess.run(
            [probe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", tmp.name],
            capture_output=True, text=True, errors="replace", cwd=path.parent)
        return float(r.stdout.strip())
    except (OSError, ValueError):
        return None
    finally:
        tmp.unlink(missing_ok=True)


def looks_truncated(path: Path, expected) -> bool:
    """True if the file is clearly shorter than the length YouTube reports."""
    if not expected:
        return False
    actual = media_duration(path)
    return actual is not None and actual < expected * 0.95 - 2


def warn_if_no_js_runtime():
    """YouTube now needs a JS runtime (Deno) for yt-dlp; without it downloads often fail with 403."""
    import shutil
    if shutil.which("deno") is None:
        console.print(Panel(
            "[bold yellow]Deno (JavaScript runtime) not found.[/bold yellow]\n"
            "yt-dlp needs it for YouTube; without it you will likely get [red]HTTP 403[/red] errors.\n\n"
            "  [cyan]winget install DenoLand.Deno[/cyan]   (then restart the terminal)\n"
            "  [cyan]pip install -U \"yt-dlp\\[default]\"[/cyan]",
            border_style="yellow",
        ))
    console.print(f"[dim]yt-dlp version: {yt_dlp.version.__version__}[/dim]")


# ── Channel helpers ────────────────────────────────────────────────────────────

CHANNEL_TABS = ("videos", "shorts", "streams")
_CHANNEL_RE = re.compile(r"youtube\.com/(@[^/?#]+|channel/[^/?#]+|c/[^/?#]+|user/[^/?#]+)", re.I)
_TAB_RE = re.compile(r"/(videos|shorts|streams|live|featured|playlists|releases|podcasts|community|about)$", re.I)


def is_channel_url(url: str) -> bool:
    """True for @handle, /channel/UC…, /c/name and /user/name URLs (not playlists or single videos)."""
    return bool(_CHANNEL_RE.search(url)) and "list=" not in url and "watch?v=" not in url


def channel_base(url: str) -> str:
    """Strip query string and any trailing tab (/videos, /shorts, …) from a channel URL."""
    url = url.split("?")[0].split("#")[0].rstrip("/")
    return _TAB_RE.sub("", url)


def resolve_url(url: str) -> str:
    """Point a bare channel URL at its Videos tab so yt-dlp returns videos, not tab playlists."""
    return f"{channel_base(url)}/videos" if is_channel_url(url) else url


class _ErrorLog:
    """yt-dlp logger that stays quiet but remembers errors (private/members-only videos, etc.)."""
    def __init__(self):
        self.errors = []
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): self.errors.append(str(msg))


def read_archive_ids(archive: Path) -> set:
    if not archive.exists():
        return set()
    return {line.split()[-1] for line in archive.read_text(encoding="utf-8").splitlines() if line.strip()}


AUDIO_FORMATS = ("mp3", "m4a")


def parse_rate(ctx, param, value):
    """Click callback: '500K' / '2M' / '1.5M' -> bytes per second (None if not given)."""
    if not value:
        return None
    rate = parse_bytes(value)
    if not rate or rate < 1024:
        raise click.BadParameter("use a size like 500K or 2M  (bytes per second)")
    return rate


def notify(title: str, body: str):
    """Best-effort desktop notification + sound. Never raises: a missing toast must not break a download."""
    env = {**os.environ, "YT_TITLE": title, "YT_BODY": body}     # env vars: no quoting problems with emoji or quotes
    try:
        if sys.platform == "win32":
            ps = (
                "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null;"
                "$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
                "$x=$t.GetElementsByTagName('text');"
                "$x[0].AppendChild($t.CreateTextNode($env:YT_TITLE))|Out-Null;"
                "$x[1].AppendChild($t.CreateTextNode($env:YT_BODY))|Out-Null;"
                "$id='{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
                "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($id).Show([Windows.UI.Notifications.ToastNotification]::new($t))"
            )
            subprocess.run(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps], env=env,
                           capture_output=True, timeout=15, creationflags=0x08000000)   # no console flash
            import winsound
            winsound.MessageBeep()
        elif sys.platform == "darwin":
            subprocess.run(["osascript", "-e", "on run {t, b}", "-e", "display notification b with title t",
                            "-e", "end run", title, body], capture_output=True, timeout=15)
        elif shutil.which("notify-send"):
            subprocess.run(["notify-send", title, body], capture_output=True, timeout=15)
        else:
            print("\a", end="")
    except Exception:
        print("\a", end="")


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


# ── Playlist layout, archive & auto-retry ──────────────────────────────────────

AUTO_PASSES = 3                      # extra passes to finish .part downloads automatically
_ID_TAG = re.compile(r"\[[A-Za-z0-9_-]{11}\](\.|$)")


def playlist_folder(opts: dict, info: dict, title: str) -> Path:
    """Ask yt-dlp itself where this playlist's files will go (same sanitising as a real download)."""
    probe = {"id": "x", "title": "t", "ext": "mp4"}
    if info.get("entries"):
        probe.update(playlist_title=title, playlist_index=1)
    with yt_dlp.YoutubeDL({**opts, "quiet": True, "logger": None}) as y:
        return Path(os.path.dirname(y.prepare_filename(probe)))


def has_legacy_names(folder: Path) -> bool:
    """True if the folder holds numbered files that were saved without a [video id]."""
    if not folder.is_dir():
        return False
    return any(re.match(r"\d+ - ", f.name) and not _ID_TAG.search(f.name)
               for f in folder.iterdir() if f.is_file())


def report_suspect(suspect: list):
    if not suspect:
        return
    console.print(f"\n[yellow]⚠ {len(suspect)} file(s) are shorter than YouTube says — may be cut off:[/yellow]")
    for n in suspect[:5]:
        console.print(f"  [dim]{escape(n[:90])}[/dim]")
    if len(suspect) > 5:
        console.print(f"  [dim]… and {len(suspect) - 5} more[/dim]")
    console.print("  Delete the file and its line in downloaded.txt, then re-run the same command.")


def read_url_file(path) -> tuple:
    """Read URLs from a text file: one per line, '#' starts a comment. Returns (urls, bad_lines)."""
    urls, bad = [], []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        token = line.split()[0]
        if not re.match(r"https?://", token):
            bad.append((n, line))
        elif token not in urls:          # drop duplicates, keep order
            urls.append(token)
    return urls, bad


# ── Add video ids to old file names ────────────────────────────────────────────

MAX_PATH = 259 if os.name == "nt" else 4000          # Windows refuses longer paths


def plan_id_renames(folder: Path, entries: list, expected_stem) -> dict:
    """Work out how to rename old-style files ('001 - Title.mp4') to '001 - Title [id].mp4'.

    entries:       the playlist's entries in order (None allowed), position = playlist index.
    expected_stem: function(index, entry) -> the name yt-dlp WOULD have given that video, without extension.

    A file only counts as belonging to a video if its name starts with that exact expected name, so a
    changed or reordered playlist is never renamed wrongly. Everything is reported, nothing is touched.
    """
    files = sorted(f for f in folder.iterdir() if f.is_file())
    plan, done, mismatched, claimed = [], 0, [], set()

    for idx, entry in enumerate(entries, 1):
        if not entry or not entry.get("id") or not entry.get("title"):
            continue
        stem = expected_stem(idx, entry)
        tagged = f"{stem} [{entry['id']}]"
        have_id = [f for f in files if f.name.startswith(tagged)]
        mine = [f for f in files if f.name.startswith(stem + ".")]
        claimed.update(have_id)
        claimed.update(mine)
        if have_id and not mine:
            done += 1
            continue
        if not mine:
            same_index = [f for f in files if f.name.startswith(f"{idx:03d} - ") and f not in claimed
                          and not _ID_TAG.search(f.name)]
            if same_index:
                mismatched.append((idx, entry["title"], same_index[0].name))
                claimed.update(same_index)
            continue                                   # not downloaded yet
        for f in mine:
            plan.append((f, f.with_name(f"{tagged}{f.name[len(stem):]}")))

    skipped = []
    ok = []
    for old, new in plan:
        if new.exists():
            skipped.append((old, "target already exists"))
        elif len(str(new)) > MAX_PATH:
            skipped.append((old, "new name would be too long for Windows"))
        else:
            ok.append((old, new))

    orphans = [f for f in files if f not in claimed and re.match(r"\d+ - ", f.name)
               and not _ID_TAG.search(f.name) and f.name != "downloaded.txt"]
    return {"rename": ok, "skipped": skipped, "done": done, "mismatched": mismatched, "orphans": orphans}


def run_with_retries(run_pass, scan_dir: Path, passes: int, errlog) -> None:
    """Run one download pass, then keep re-running while half-finished .part files remain.

    Finished videos are skipped (archive) and .part files resume, so each extra pass is cheap.
    """
    run_pass()
    for n in range(1, passes + 1):
        left = find_incomplete(scan_dir)
        if not left:
            return
        console.print(f"\n[yellow]↻ {len(left)} unfinished download(s) — retry pass {n}/{passes}…[/yellow]")
        time.sleep(5)
        errlog.errors.clear()        # only report errors that survive the last pass
        run_pass()


# ── CLI ────────────────────────────────────────────────────────────────────────

@click.group()
def cli():
    """🎬  YouTube Playlist Downloader  (Windows Edition)"""
    pass


# ── download ──────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("url")
@click.option("-o", "--output", default=None,
              help="Output folder  (default from config, usually ~/Videos/YT Downloads)")
@click.option("-q", "--quality", default=None,
              type=click.Choice(["best", "1080p", "720p", "480p", "360p"]),
              help="Video quality  (default from config)")
@click.option("--resume/--no-resume", default=True, show_default=True,
              help="Resume interrupted downloads")
@click.option("--start", default=1, show_default=True,
              help="Start from this playlist index")
@click.option("--end", default=0, show_default=True,
              help="End at this playlist index  (0 = all)")
@click.option("--archive/--no-archive", default=True, show_default=True,
              help="Remember finished videos (downloaded.txt) so re-runs only touch what is missing")
@click.option("--audio", type=click.Choice(AUDIO_FORMATS), default=None,
              help="Audio only: save as mp3 or m4a instead of video (own '(audio)' folder)")
@click.option("--subs", default=None, metavar="LANGS",
              help="Also save subtitles, e.g. --subs en  or  --subs en,fr  (real captions, else auto-generated)")
@click.option("--retry-passes", default=AUTO_PASSES, show_default=True,
              help="Extra automatic passes to finish unfinished downloads  (0 = off)")
@click.option("--embed", is_flag=True,
              help="Embed cover art, chapters and tags (title, channel, date) in each file")
@click.option("--rate-limit", callback=parse_rate, default=None, metavar="RATE",
              help="Max download speed, e.g. 500K or 2M (per second)")
@click.option("--notify", "notify_when_done", is_flag=True,
              help="Desktop notification and sound when everything is finished")
@click.option("-y", "--yes", is_flag=True, help="Skip the confirmation prompt")
@click.pass_context
def download(ctx, url, output, quality, resume, start, end, archive, audio, subs, retry_passes, embed,
             rate_limit, notify_when_done, yes):
    """Download a YouTube playlist or single video (channel URLs switch to channel mode)."""

    if is_channel_url(url):
        console.print("[cyan]Channel URL detected — switching to channel mode.[/cyan]")
        return ctx.invoke(channel, url=url, output=output, quality=quality,
                          tabs="videos", limit=end, since=None, browser=None, yes=yes,
                          retry_passes=retry_passes, audio=audio, subs=subs, embed=embed,
                          rate_limit=rate_limit, notify_when_done=notify_when_done)

    require_ffmpeg()
    warn_if_old_ytdlp()

    cfg = load_config()
    output  = safe_win_path(output  or cfg.get("default_output", DEFAULT_OUTPUT))
    quality = quality or cfg.get("default_quality", "best")

    console.print(Panel.fit(
        "🎬  [bold cyan]YouTube Playlist Downloader[/bold cyan]  —  Windows Edition",
        border_style="cyan"
    ))

    console.print("\n[yellow]⏳ Fetching playlist info…[/yellow]")
    info = fetch_playlist_info(url)

    if not info:
        console.print("[red]❌  Could not retrieve playlist. Check the URL and try again.[/red]")
        return "failed"

    title   = info.get("title", "Unknown Playlist")
    entries = info.get("entries") or [info]
    entries = [e for e in entries if e]        # skip None entries
    entries = entries[max(start, 1) - 1:(end or None)]
    total   = len(entries)

    # Where files go, naming style, and which videos are already finished
    langs  = parse_langs(subs)
    warn_embed(embed, audio)
    opts   = build_ydl_opts(output, quality, resume, start, end, audio=audio, subs=langs, embed=embed,
                            rate_limit=rate_limit)
    folder = playlist_folder(opts, info, title)
    legacy = has_legacy_names(folder)
    if legacy:   # older downloads have no [id] in the name — keep that so nothing downloads twice
        opts = build_ydl_opts(output, quality, resume, start, end, id_in_name=False, audio=audio, subs=langs, embed=embed,
                                rate_limit=rate_limit)
    archive_file = folder / "downloaded.txt"
    done_ids = read_archive_ids(archive_file) if archive else set()
    pending  = sum(1 for e in entries if e.get("id") not in done_ids)

    # Summary panel
    tbl = Table(show_header=False, box=None, padding=(0, 2))
    tbl.add_row("[bold]Playlist[/bold]",  f"[green]{title}[/green]")
    tbl.add_row("[bold]Videos[/bold]",    str(total))
    if archive:
        tbl.add_row("[bold]Already have[/bold]", str(total - pending))
        tbl.add_row("[bold]To download[/bold]",  f"[cyan]{pending}[/cyan]")
    tbl.add_row("[bold]Quality[/bold]",   f"audio only ({audio})" if audio else quality)
    if langs:
        tbl.add_row("[bold]Subtitles[/bold]", ", ".join(langs))
    if embed:
        tbl.add_row("[bold]Embed[/bold]", "cover art, chapters, tags")
    tbl.add_row("[bold]Output[/bold]",    output)
    tbl.add_row("[bold]Resume[/bold]",    "✔ Yes" if resume else "✘ No")
    if start > 1 or end > 0:
        rng = f"{start} → {end if end else total}"
        tbl.add_row("[bold]Range[/bold]", rng)
    console.print(tbl)
    console.print()

    if legacy:
        console.print("[dim]ℹ Files in this folder use the old naming (no video id) — keeping it so nothing downloads twice.[/dim]\n")

    if archive and pending == 0:
        repair_folder(folder)
        console.print("[bold green]✅ Already up to date — nothing left to download.[/bold green]")
        return "uptodate"

    if not yes and not click.confirm("▶  Start download?", default=True):
        console.print("[yellow]Aborted.[/yellow]")
        return "aborted"

    Path(output).mkdir(parents=True, exist_ok=True)
    if archive:
        opts["download_archive"] = str(archive_file)

    downloaded = []
    suspect = []        # finished files shorter than YouTube says
    errlog = _ErrorLog()
    _seen_merged = set()

    def postprocessor_hook(d):
        """Fires once per video after ffmpeg merges streams — correct count."""
        if d.get("status") == "finished" and d.get("postprocessor") == "MoveFiles":
            fpath = d.get("info_dict", {}).get("filepath") or d.get("filepath", "")
            if fpath and fpath not in _seen_merged:
                _seen_merged.add(fpath)
                downloaded.append(fpath)
                if looks_truncated(Path(fpath), d.get("info_dict", {}).get("duration")):
                    suspect.append(Path(fpath).name)

    opts["logger"]              = errlog   # keep download/merge errors instead of hiding them
    opts["postprocessor_hooks"] = [postprocessor_hook]

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[cyan]{task.completed}/{task.total}[/cyan]"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("[green]Downloading…", total=pending if archive else total)

        base_pp_hook = opts["postprocessor_hooks"][0]

        def rich_pp_hook(d):
            base_pp_hook(d)
            if d.get("status") == "finished" and d.get("postprocessor") == "MoveFiles":
                title_str = (d.get("info_dict", {}).get("title") or
                             Path(d.get("filepath", "?")).stem)[:55]
                progress.update(task, advance=1, description=f"[green]✓ {title_str}")

        opts["postprocessor_hooks"] = [rich_pp_hook]

        def one_pass():
            with yt_dlp.YoutubeDL(opts) as ydl:
                try:
                    ydl.download([url])
                except Exception as e:
                    console.print(f"[red]Download error: {e}[/red]")

        try:
            run_with_retries(one_pass, folder, retry_passes, errlog)
        except KeyboardInterrupt:
            console.print("\n[yellow]⏸  Interrupted — re-run the same command to continue "
                          "(finished videos are skipped, half-done ones resume).[/yellow]")

    # ── Fix any video/audio files ffmpeg failed to merge ──────────────────────
    repair_folder(folder)

    # ── Result summary ────────────────────────────────────────────────────────
    failed = [clean_msg(m) for m in errlog.errors]
    console.print(
        f"\n[bold green]✅ Done![/bold green]  "
        f"[green]{len(downloaded)}[/green] downloaded  •  "
        f"[red]{len(failed)}[/red] failed"
    )

    if failed:
        for f in failed[:5]:
            console.print(f"  [red]✗[/red] {escape(f[:110])}")
        if len(failed) > 5:
            console.print(f"  [dim]… and {len(failed)-5} more[/dim]")

    save_history({
        "date":       datetime.now().strftime("%Y-%m-%d %H:%M"),
        "url":        url,
        "playlist":   title,
        "total":      total,
        "downloaded": len(downloaded),
        "failed":     len(failed),
        "output":     output,
        "quality":    f"audio-{audio}" if audio else quality,
    })

    report_suspect(suspect)

    console.print(f"\n📁  Saved to: [cyan]{output}[/cyan]")
    console.print(f"    (Open in Explorer:  [dim]explorer \"{output}\"[/dim])")
    if notify_when_done:
        notify("YouTube download finished", f"{title}: {len(downloaded)} new, {len(failed)} failed")


# ── channel ───────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("url")
@click.option("-o", "--output", default=None,
              help="Output folder  (default from config)")
@click.option("-q", "--quality", default=None,
              type=click.Choice(["best", "1080p", "720p", "480p", "360p"]),
              help="Video quality  (default from config)")
@click.option("--tabs", default="videos", show_default=True,
              help="Channel tabs to grab: videos, shorts, streams (comma-separated) or 'all'")
@click.option("--limit", default=0, show_default=True,
              help="Max videos per tab, newest first  (0 = everything)")
@click.option("--since", default=None,
              help="Only videos uploaded on/after this date, YYYYMMDD")
@click.option("--cookies-from-browser", "browser", default=None,
              type=click.Choice(["chrome", "edge", "firefox", "brave", "opera"]),
              help="Use your browser's YouTube login (helps with 403 / bot checks)")
@click.option("-y", "--yes", is_flag=True, help="Skip the confirmation prompt")
@click.option("--audio", type=click.Choice(AUDIO_FORMATS), default=None,
              help="Audio only: save as mp3 or m4a instead of video (own '(audio)' folder)")
@click.option("--subs", default=None, metavar="LANGS",
              help="Also save subtitles, e.g. --subs en  or  --subs en,fr  (real captions, else auto-generated)")
@click.option("--retry-passes", default=AUTO_PASSES, show_default=True,
              help="Extra automatic passes to finish unfinished downloads  (0 = off)")
@click.option("--rate-limit", callback=parse_rate, default=None, metavar="RATE",
              help="Max download speed, e.g. 500K or 2M (per second)")
@click.option("--notify", "notify_when_done", is_flag=True,
              help="Desktop notification and sound when everything is finished")
@click.option("--embed", is_flag=True,
              help="Embed cover art, chapters and tags (title, channel, date) in each file")
def channel(url, output, quality, tabs, limit, since, browser, yes, audio, subs, retry_passes, embed,
            rate_limit, notify_when_done):
    """Download ALL videos from a channel.

    Re-run the same command later to fetch only new uploads
    (finished videos are tracked in <channel folder>\\downloaded.txt).
    """
    require_ffmpeg()
    warn_if_old_ytdlp()
    warn_if_no_js_runtime()

    cfg = load_config()
    output  = safe_win_path(output or cfg.get("default_output", DEFAULT_OUTPUT))
    quality = quality or cfg.get("default_quality", "best")

    wanted = list(CHANNEL_TABS) if tabs.strip().lower() == "all" \
        else [t.strip().lower() for t in tabs.split(",") if t.strip()]
    bad = [t for t in wanted if t not in CHANNEL_TABS]
    if bad or not wanted:
        raise click.BadParameter(f"choose from: {', '.join(CHANNEL_TABS)} or 'all'", param_hint="--tabs")
    if since and not re.fullmatch(r"\d{8}", since):
        raise click.BadParameter("use YYYYMMDD, e.g. 20240101", param_hint="--since")

    console.print(Panel.fit(
        "📺  [bold cyan]YouTube Channel Downloader[/bold cyan]  —  Windows Edition",
        border_style="cyan"
    ))

    # ── Scan the channel ──────────────────────────────────────────────────────
    base = channel_base(url)
    console.print("\n[yellow]⏳ Scanning channel (large channels can take a minute)…[/yellow]")

    tab_entries, channel_name = {}, None
    for tab in wanted:
        data = fetch_playlist_info(f"{base}/{tab}")
        entries = [e for e in ((data or {}).get("entries") or []) if e]
        if not entries:
            console.print(f"  [dim]{tab}: nothing found, skipping[/dim]")
            continue
        channel_name = channel_name or data.get("channel") or data.get("uploader") or data.get("title")
        tab_entries[tab] = entries[:limit] if limit > 0 else entries

    if not tab_entries:
        console.print("[red]❌  No videos found. Check the channel URL and try again.[/red]")
        return "failed"

    channel_name = channel_name or base.rsplit("/", 1)[-1]
    chan_dir = Path(output) / (sanitize_filename(channel_name, is_id=False) + (" (audio)" if audio else ""))
    archive  = chan_dir / "downloaded.txt"
    done_ids = read_archive_ids(archive)

    found   = sum(len(v) for v in tab_entries.values())
    pending = sum(1 for v in tab_entries.values() for e in v if e.get("id") not in done_ids)

    tbl = Table(show_header=False, box=None, padding=(0, 2))
    tbl.add_row("[bold]Channel[/bold]",  f"[green]{channel_name}[/green]")
    for tab, v in tab_entries.items():
        tbl.add_row(f"[bold]{tab.capitalize()}[/bold]", str(len(v)))
    tbl.add_row("[bold]Already have[/bold]", str(found - pending))
    tbl.add_row("[bold]To download[/bold]",  f"[cyan]{pending}[/cyan]" + ("  (before --since filter)" if since else ""))
    tbl.add_row("[bold]Quality[/bold]",  f"audio only ({audio})" if audio else quality)
    warn_embed(embed, audio)
    langs = parse_langs(subs)
    if langs:
        tbl.add_row("[bold]Subtitles[/bold]", ", ".join(langs))
    if embed:
        tbl.add_row("[bold]Embed[/bold]", "cover art, chapters, tags")
    tbl.add_row("[bold]Output[/bold]",   str(chan_dir))
    console.print(tbl)
    console.print()

    if pending == 0:
        console.print("[bold green]✅ Already up to date — nothing new to download.[/bold green]")
        return "uptodate"

    if not yes and not click.confirm("▶  Start download?", default=True):
        console.print("[yellow]Aborted.[/yellow]")
        return "aborted"

    chan_dir.mkdir(parents=True, exist_ok=True)
    errlog = _ErrorLog()
    downloaded = []
    suspect = []        # finished files shorter than YouTube says

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[cyan]{task.completed}[/cyan] done"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("[green]Starting…", total=None if since else pending)

        def pp_hook(d):
            if d.get("status") == "finished" and d.get("postprocessor") == "MoveFiles":
                info_d = d.get("info_dict", {})
                downloaded.append(info_d.get("id", ""))
                fpath = info_d.get("filepath") or d.get("filepath")
                if fpath and looks_truncated(Path(fpath), info_d.get("duration")):
                    suspect.append(Path(fpath).name)
                progress.update(task, advance=1,
                                description=f"[green]✓ {(info_d.get('title') or '?')[:55]}")

        def one_pass():
                for tab in tab_entries:
                    target = chan_dir / tab if len(tab_entries) > 1 else chan_dir
                    target.mkdir(parents=True, exist_ok=True)
                    tmpl = os.path.join(
                        str(target).replace("%", "%%"),
                        "%(upload_date>%Y-%m-%d)s - %(title).100s [%(id)s].%(ext)s",
                    )

                    opts = build_ydl_opts(output, quality, True, 1, limit, audio=audio, subs=langs, embed=embed,
                                           rate_limit=rate_limit)
                    opts.update({
                        "outtmpl": tmpl,
                        "download_archive": str(archive),   # skip anything already downloaded
                        "retries": 10,
                        "fragment_retries": 10,
                        "sleep_interval": 1,                 # be polite on big channels
                        "max_sleep_interval": 4,
                        "logger": errlog,
                        "postprocessor_hooks": [pp_hook],
                    })
                    if browser:
                        opts["cookiesfrombrowser"] = (browser,)
                    if since:
                        opts["dateafter"] = since
                        opts["breakonreject"] = True         # newest-first, so stop at the cutoff

                    with yt_dlp.YoutubeDL(opts) as ydl:
                        ydl.download([f"{base}/{tab}"])

        try:
            run_with_retries(one_pass, chan_dir, retry_passes, errlog)
        except KeyboardInterrupt:
            console.print("\n[yellow]⏸  Interrupted — re-run the same command to continue "
                          "(finished videos are skipped).[/yellow]")

    # ── Fix any video/audio files ffmpeg failed to merge ──────────────────────
    repair_folder(chan_dir)

    # ── Result summary ────────────────────────────────────────────────────────
    failed = [clean_msg(m) for m in errlog.errors]
    console.print(
        f"\n[bold green]✅ Done![/bold green]  "
        f"[green]{len(downloaded)}[/green] downloaded  •  "
        f"[red]{len(failed)}[/red] errors"
    )
    for msg in failed[:5]:
        console.print(f"  [red]✗[/red] {escape(msg[:110])}")
    if len(failed) > 5:
        console.print(f"  [dim]… and {len(failed) - 5} more[/dim]")
    if failed and all("403" in m for m in failed):
        console.print(
            "\n[yellow]Every error is HTTP 403 — this is YouTube blocking yt-dlp, not your files.[/yellow]\n"
            "  1. [cyan]pip install -U \"yt-dlp\\[default]\"[/cyan]\n"
            "  2. [cyan]winget install DenoLand.Deno[/cyan]  (restart terminal)\n"
            "  3. Retry, adding [cyan]--cookies-from-browser chrome[/cyan] if it persists"
        )

    save_history({
        "date":       datetime.now().strftime("%Y-%m-%d %H:%M"),
        "url":        url,
        "playlist":   f"{channel_name} (channel)",
        "total":      found,
        "downloaded": len(downloaded),
        "failed":     len(failed),
        "output":     str(chan_dir),
        "quality":    f"audio-{audio}" if audio else quality,
    })

    report_suspect(suspect)

    console.print(f"\n📁  Saved to: [cyan]{chan_dir}[/cyan]")
    console.print(f"    (Open in Explorer:  [dim]explorer \"{chan_dir}\"[/dim])")
    if notify_when_done:
        notify("YouTube download finished", f"{channel_name}: {len(downloaded)} new, {len(failed)} failed")


# ── info ──────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("url")
def info(url):
    """Preview playlist contents without downloading."""
    console.print("[yellow]⏳ Fetching info…[/yellow]")
    data = fetch_playlist_info(resolve_url(url))

    if not data:
        console.print("[red]Could not fetch info.[/red]")
        return

    entries = [e for e in (data.get("entries") or [data]) if e]

    tbl = Table(title=f"📋  {data.get('title', 'Unknown')}", show_lines=True)
    tbl.add_column("#",        width=5,  style="dim")
    tbl.add_column("Title",    style="green")
    tbl.add_column("Duration", width=10)
    tbl.add_column("Video ID", width=14, style="dim")

    for i, e in enumerate(entries[:60], 1):
        dur = e.get("duration")
        dur_str = f"{int(dur)//60}:{int(dur)%60:02d}" if dur else "?"
        tbl.add_row(str(i), (e.get("title") or "Unknown")[:65], dur_str, e.get("id", ""))

    console.print(tbl)
    if len(entries) > 60:
        console.print(f"[dim]… and {len(entries)-60} more videos[/dim]")


# ── repair ────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("folder", required=False)
def repair(folder):
    """Merge leftover video + audio files (name.f137.mp4 + name.f140.m4a) into one .mp4.

    FOLDER defaults to your configured output folder. Searches subfolders too.
    """
    require_ffmpeg()
    cfg = load_config()
    root = Path(safe_win_path(folder or cfg.get("default_output", DEFAULT_OUTPUT)))
    if not root.is_dir():
        console.print(f"[red]Folder not found: {escape(str(root))}[/red]")
        return

    console.print(f"[yellow]⏳ Scanning {escape(str(root))}…[/yellow]")
    found, fixed, incomplete = repair_folder(root)
    if found == 0 and incomplete == 0:
        console.print("[green]✅ No split or incomplete files found.[/green]")
    elif found:
        console.print(f"\n[bold green]Done![/bold green]  {fixed}/{found} merged.")


# ── update ────────────────────────────────────────────────────────────────────

@cli.command()
def update():
    """Upgrade yt-dlp to the latest version (fixes most 403 and broken-download errors)."""
    console.print(f"[yellow]⏳ Updating yt-dlp (now {YTDLP_VERSION})…[/yellow]")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-U", "--quiet", "yt-dlp[default]"])
    if r.returncode != 0:
        console.print("[red]Update failed.[/red] Try manually: pip install -U \"yt-dlp\\[default]\"")
        return
    new = subprocess.run(
        [sys.executable, "-c", "from yt_dlp.version import __version__ as v; print(v)"],
        capture_output=True, text=True).stdout.strip()
    if new and new != YTDLP_VERSION:
        console.print(f"[bold green]✅ Updated:[/bold green] {YTDLP_VERSION} → {new}")
    else:
        console.print("[green]✅ Already on the latest yt-dlp.[/green]")


# ── batch ─────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("file", type=click.Path(exists=True, dir_okay=False))
@click.option("-o", "--output", default=None, help="Output folder  (default from config)")
@click.option("-q", "--quality", default=None, type=click.Choice(list(QUALITY_OPTIONS)),
              help="Video quality  (default from config)")
@click.option("--audio", type=click.Choice(AUDIO_FORMATS), default=None,
              help="Audio only: save as mp3 or m4a instead of video")
@click.option("--subs", default=None, metavar="LANGS", help="Also save subtitles, e.g. --subs en")
@click.option("--embed", is_flag=True, help="Embed cover art, chapters and tags in each file")
@click.option("--rate-limit", callback=parse_rate, default=None, metavar="RATE",
              help="Max download speed, e.g. 500K or 2M (per second)")
@click.option("--notify", "notify_when_done", is_flag=True,
              help="Desktop notification and sound when everything is finished")
@click.option("--retry-passes", default=AUTO_PASSES, show_default=True,
              help="Extra automatic passes to finish unfinished downloads  (0 = off)")
@click.pass_context
def batch(ctx, file, output, quality, audio, subs, embed, rate_limit, notify_when_done, retry_passes):
    """Download every URL in FILE with no prompts. One URL per line, '#' for comments.

    Playlists, channels and single videos can be mixed. A failure on one URL never
    stops the rest. Re-running is cheap: finished videos are skipped.
    """
    require_ffmpeg()
    urls, bad = read_url_file(file)
    for n, line in bad:
        console.print(f"[yellow]⚠ line {n} skipped (not a http(s) URL): {escape(line[:60])}[/yellow]")
    if not urls:
        console.print("[red]❌  No URLs found in the file.[/red]")
        return
    console.print(f"[cyan]📋 {len(urls)} URL(s) from {escape(str(file))}[/cyan]\n")

    results = []                          # (url, icon, note)
    for i, url in enumerate(urls, 1):
        console.rule(f"[bold]{i}/{len(urls)}[/bold]  {escape(url[:70])}")
        before = len(load_history())
        try:
            status = ctx.invoke(download, url=url, output=output, quality=quality, resume=True,
                                start=1, end=0, archive=True, audio=audio, subs=subs,
                                retry_passes=retry_passes, embed=embed, rate_limit=rate_limit,
                                notify_when_done=False, yes=True)     # one notification at the very end
            history = load_history()
            if status == "failed":
                results.append((url, "[red]✗[/red]", "could not read this URL"))
            elif status == "uptodate":
                results.append((url, "[green]•[/green]", "already up to date"))
            elif len(history) > before:
                e = history[-1]
                bad_n = e.get("failed", 0)
                results.append((url, "[yellow]⚠[/yellow]" if bad_n else "[green]✓[/green]",
                                f"{e.get('downloaded', 0)} new, {bad_n} failed"))
            else:
                results.append((url, "[dim]–[/dim]", "nothing done"))
        except KeyboardInterrupt:
            results.append((url, "[yellow]⏸[/yellow]", "interrupted"))
            console.print("\n[yellow]⏸  Batch stopped. Re-run the same command to continue "
                          "(finished videos are skipped).[/yellow]")
            break
        except (Exception, SystemExit) as e:
            results.append((url, "[red]✗[/red]", f"error: {str(e)[:70] or type(e).__name__}"))

    console.print()
    tbl = Table(title="Batch summary", box=box.ROUNDED, show_lines=False)
    tbl.add_column("", width=2)
    tbl.add_column("URL", overflow="ellipsis", max_width=50)
    tbl.add_column("Result")
    for url, icon, note in results:
        tbl.add_row(icon, escape(url), escape(note))
    console.print(tbl)
    skipped = len(urls) - len(results)
    if skipped:
        console.print(f"[yellow]{skipped} URL(s) not started (batch was stopped).[/yellow]")
    if notify_when_done:
        problems = sum(1 for _, icon, _ in results if "✗" in icon or "⚠" in icon) + skipped
        notify("YouTube batch finished", f"{len(urls)} URL(s) processed, {problems} need attention")


# ── notify-test ───────────────────────────────────────────────────────────────

@cli.command("notify-test")
def notify_test():
    """Send a test notification, to check that --notify works on this computer."""
    console.print("[yellow]Sending a test notification…[/yellow]")
    notify("YT Downloader", "Notifications work ✅")
    console.print("Did a notification pop up (or did you hear a beep)? If not, --notify is silent here.")


# ── add-ids ───────────────────────────────────────────────────────────────────

@cli.command("add-ids")
@click.argument("url")
@click.option("-o", "--output", default=None, help="Output folder  (default from config)")
@click.option("--folder", type=click.Path(file_okay=False), default=None,
              help="The playlist folder to fix  (default: worked out from the URL and --output)")
@click.option("--apply", "apply_", is_flag=True, help="Really rename. Without this it only shows what it would do.")
def add_ids(url, output, folder, apply_):
    """Add [video id] to the names of files from older versions, so every file maps to its video.

    Needs the playlist URL the folder was downloaded from. Files are matched by playlist number AND
    exact title; anything that does not match is left alone. Dry run by default.
    """
    cfg = load_config()
    output = safe_win_path(output or cfg.get("default_output", DEFAULT_OUTPUT))
    console.print("[yellow]⏳ Fetching playlist info…[/yellow]")
    info = fetch_playlist_info(url)
    if not info or not info.get("entries"):
        console.print("[red]❌  Could not read a playlist from that URL (single videos have nothing to fix).[/red]")
        return
    title = info.get("title", "Unknown Playlist")
    entries = list(info["entries"])

    opts = build_ydl_opts(output, "best", True, 1, 0, id_in_name=False)       # the OLD naming
    target = Path(safe_win_path(folder)) if folder else playlist_folder(opts, info, title)
    if not target.is_dir():
        console.print(f"[red]Folder not found: {escape(str(target))}[/red]\n"
                      "Use --output or --folder to point at it.")
        return

    with yt_dlp.YoutubeDL({**opts, "quiet": True, "logger": None}) as ydl:
        def expected_stem(idx, entry):
            probe = {"id": entry["id"], "title": entry["title"], "ext": "mp4",
                     "playlist_title": title, "playlist_index": idx}
            return Path(ydl.prepare_filename(probe)).name[:-len(".mp4")]
        plan = plan_id_renames(target, entries, expected_stem)

    console.print(f"\n[bold]Folder:[/bold] {escape(str(target))}")
    console.print(f"  [green]{len(plan['rename'])}[/green] file(s) to rename   "
                  f"[dim]{plan['done']} video(s) already have an id[/dim]")
    for old, new in plan["rename"][:6]:
        console.print(f"  [dim]{escape(old.name[-60:])}[/dim]\n    → {escape(new.name[-60:])}")
    if len(plan["rename"]) > 6:
        console.print(f"  [dim]… and {len(plan['rename']) - 6} more[/dim]")
    for old, why in plan["skipped"][:5]:
        console.print(f"  [yellow]skipped[/yellow] {escape(old.name[:50])} — {why}")
    if plan["mismatched"]:
        console.print(f"\n[yellow]⚠ {len(plan['mismatched'])} video(s) have a file with the same number but a "
                      f"different title (playlist changed?). Left alone:[/yellow]")
        for idx, want, found in plan["mismatched"][:5]:
            console.print(f"  [dim]#{idx}: playlist says “{escape(want[:40])}”, folder has “{escape(found[:40])}”[/dim]")
    if plan["orphans"]:
        console.print(f"\n[dim]{len(plan['orphans'])} other numbered file(s) match no playlist video and stay as they are.[/dim]")

    if not plan["rename"]:
        console.print("\n[green]✅ Nothing to rename.[/green]")
        return
    if not apply_:
        console.print("\n[bold yellow]Dry run — nothing was renamed.[/bold yellow] Add [cyan]--apply[/cyan] to do it.")
        return

    renamed, failed = 0, 0
    for old, new in plan["rename"]:
        try:
            old.rename(new)
            renamed += 1
        except OSError as e:
            failed += 1
            console.print(f"  [red]✗[/red] {escape(old.name[:50])}: {escape(str(e)[:60])}")
    console.print(f"\n[bold green]✅ Renamed {renamed} file(s).[/bold green]" + (f"  [red]{failed} failed[/red]" if failed else ""))
    console.print("Your next [cyan]yt download[/cyan] of this playlist uses the new names automatically.")


# ── history ───────────────────────────────────────────────────────────────────

@cli.command()
@click.option("-n", "--last", default=20, show_default=True, help="Number of entries to show")
def history(last):
    """Show recent download history."""
    entries = load_history()

    if not entries:
        console.print("[yellow]No download history yet.[/yellow]")
        return

    tbl = Table(title="📜  Download History", show_lines=True)
    tbl.add_column("#",          style="dim",   width=4)
    tbl.add_column("Date",       style="cyan",  width=17)
    tbl.add_column("Playlist",   style="green")
    tbl.add_column("Quality",    width=8)
    tbl.add_column("Result",     justify="right", width=12)
    tbl.add_column("Output folder", style="dim")

    for i, e in enumerate(reversed(entries[-last:]), 1):
        result = f"[green]{e.get('downloaded',0)}[/green]/[dim]{e.get('total','?')}[/dim]"
        if e.get("failed"):
            result += f"  [red]({e['failed']} ✗)[/red]"
        tbl.add_row(
            str(i),
            e.get("date", "")[:16],
            e.get("playlist", "Unknown")[:45],
            e.get("quality", "?"),
            result,
            e.get("output", "")[:40],
        )

    console.print(tbl)
    console.print(f"\n[dim]History file: {HISTORY_FILE}[/dim]")


# ── clear-history ─────────────────────────────────────────────────────────────

@cli.command("clear-history")
def clear_history():
    """Delete all download history."""
    if not HISTORY_FILE.exists():
        console.print("[yellow]No history to clear.[/yellow]")
        return
    if click.confirm("⚠  Delete all download history?", default=False):
        HISTORY_FILE.unlink()
        console.print("[green]✔ History cleared.[/green]")
    else:
        console.print("[yellow]Cancelled.[/yellow]")


# ── config ────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--output",  default=None, help="Set default output folder")
@click.option("--quality", default=None,
              type=click.Choice(["best", "1080p", "720p", "480p", "360p"]),
              help="Set default quality")
def config(output, quality):
    """View or update default settings."""
    cfg = load_config()

    if not output and not quality:
        # Just show current config
        tbl = Table(show_header=False, box=None, padding=(0, 2))
        tbl.add_row("[bold]Default output[/bold]",  cfg.get("default_output", DEFAULT_OUTPUT))
        tbl.add_row("[bold]Default quality[/bold]", cfg.get("default_quality", "best"))
        tbl.add_row("[bold]Config file[/bold]",     str(CONFIG_FILE))
        console.print(Panel(tbl, title="⚙  Current Config", border_style="blue"))
        return

    if output:
        cfg["default_output"] = safe_win_path(output)
        console.print(f"[green]✔[/green] Default output set to: [cyan]{cfg['default_output']}[/cyan]")
    if quality:
        cfg["default_quality"] = quality
        console.print(f"[green]✔[/green] Default quality set to: [cyan]{quality}[/cyan]")

    save_config(cfg)


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cli()
