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
import json
import sys
import shutil
import click
from datetime import datetime
from pathlib import Path, WindowsPath

try:
    import yt_dlp
    from yt_dlp.utils import sanitize_filename
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn, DownloadColumn
    from rich.table import Table
    from rich.panel import Panel
    from rich.prompt import Prompt
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


# ── Config & history stored in %APPDATA%\YTDownloader ──────────────────────────
APP_DIR      = Path(os.environ.get("APPDATA", Path.home())) / "YTDownloader"
HISTORY_FILE = APP_DIR / "history.json"
CONFIG_FILE  = APP_DIR / "config.json"
APP_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_OUTPUT = str(Path.home() / "Videos" / "YT Downloads")

# Format strings: prefer pre-merged mp4 stream, then merge with ffmpeg, then any single-file fallback
QUALITY_OPTIONS = {
    "best":  "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
    "1080p": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]",
    "720p":  "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio/best[height<=720]",
    "480p":  "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=480]+bestaudio/best[height<=480]",
    "360p":  "bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=360]+bestaudio/best[height<=360]",
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
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []


def save_history(entry: dict):
    history = load_history()
    history.append(entry)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


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


def build_ydl_opts(output_dir: str, quality: str, resume: bool,
                   playlist_start: int, playlist_end: int) -> dict:
    fmt = QUALITY_OPTIONS.get(quality, QUALITY_OPTIONS["best"])

    # Windows-safe output template (avoids illegal filename chars)
    outtmpl = os.path.join(
        output_dir,
        "%(playlist_title)s",
        "%(playlist_index)03d - %(title)s.%(ext)s",
    )

    opts = {
        "format": fmt,
        "outtmpl": outtmpl,
        "continuedl": resume,
        "concurrent_fragment_downloads": 4,
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

    if playlist_start > 1:
        opts["playliststart"] = playlist_start
    if playlist_end > 0:
        opts["playlistend"] = playlist_end

    return opts


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
@click.pass_context
def download(ctx, url, output, quality, resume, start, end):
    """Download a YouTube playlist or single video (channel URLs switch to channel mode)."""

    if is_channel_url(url):
        console.print("[cyan]Channel URL detected — switching to channel mode.[/cyan]")
        return ctx.invoke(channel, url=url, output=output, quality=quality,
                          tabs="videos", limit=end, since=None, yes=False)

    require_ffmpeg()

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
        return

    title   = info.get("title", "Unknown Playlist")
    entries = info.get("entries") or [info]
    total   = len([e for e in entries if e])   # skip None entries

    # Summary panel
    tbl = Table(show_header=False, box=None, padding=(0, 2))
    tbl.add_row("[bold]Playlist[/bold]",  f"[green]{title}[/green]")
    tbl.add_row("[bold]Videos[/bold]",    str(total))
    tbl.add_row("[bold]Quality[/bold]",   quality)
    tbl.add_row("[bold]Output[/bold]",    output)
    tbl.add_row("[bold]Resume[/bold]",    "✔ Yes" if resume else "✘ No")
    if start > 1 or end > 0:
        rng = f"{start} → {end if end else total}"
        tbl.add_row("[bold]Range[/bold]", rng)
    console.print(tbl)
    console.print()

    if not click.confirm("▶  Start download?", default=True):
        console.print("[yellow]Aborted.[/yellow]")
        return

    Path(output).mkdir(parents=True, exist_ok=True)
    opts = build_ydl_opts(output, quality, resume, start, end)

    downloaded, failed = [], []
    _seen_merged = set()

    def progress_hook(d):
        # Only track errors here; counting is done in postprocessor_hook
        if d["status"] == "error":
            failed.append(d.get("filename", "unknown"))

    def postprocessor_hook(d):
        """Fires once per video after ffmpeg merges streams — correct count."""
        if d.get("status") == "finished" and d.get("postprocessor") == "MoveFiles":
            fpath = d.get("info_dict", {}).get("filepath") or d.get("filepath", "")
            if fpath and fpath not in _seen_merged:
                _seen_merged.add(fpath)
                downloaded.append(fpath)

    opts["progress_hooks"]      = [progress_hook]
    opts["postprocessor_hooks"] = [postprocessor_hook]

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[cyan]{task.completed}/{task.total}[/cyan]"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("[green]Downloading…", total=total)

        base_pp_hook = opts["postprocessor_hooks"][0]

        def rich_pp_hook(d):
            base_pp_hook(d)
            if d.get("status") == "finished" and d.get("postprocessor") == "MoveFiles":
                title_str = (d.get("info_dict", {}).get("title") or
                             Path(d.get("filepath", "?")).stem)[:55]
                progress.update(task, advance=1, description=f"[green]✓ {title_str}")

        opts["postprocessor_hooks"] = [rich_pp_hook]

        with yt_dlp.YoutubeDL(opts) as ydl:
            try:
                ydl.download([url])
            except Exception as e:
                console.print(f"[red]Download error: {e}[/red]")

    # ── Result summary ────────────────────────────────────────────────────────
    console.print(
        f"\n[bold green]✅ Done![/bold green]  "
        f"[green]{len(downloaded)}[/green] downloaded  •  "
        f"[red]{len(failed)}[/red] failed"
    )

    if failed:
        for f in failed[:5]:
            console.print(f"  [red]✗[/red] {f}")
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
        "quality":    quality,
    })

    console.print(f"\n📁  Saved to: [cyan]{output}[/cyan]")
    console.print(f"    (Open in Explorer:  [dim]explorer \"{output}\"[/dim])")


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
@click.option("-y", "--yes", is_flag=True, help="Skip the confirmation prompt")
def channel(url, output, quality, tabs, limit, since, yes):
    """Download ALL videos from a channel.

    Re-run the same command later to fetch only new uploads
    (finished videos are tracked in <channel folder>\\downloaded.txt).
    """
    require_ffmpeg()

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
        return

    channel_name = channel_name or base.rsplit("/", 1)[-1]
    chan_dir = Path(output) / sanitize_filename(channel_name, is_id=False)
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
    tbl.add_row("[bold]Quality[/bold]",  quality)
    tbl.add_row("[bold]Output[/bold]",   str(chan_dir))
    console.print(tbl)
    console.print()

    if pending == 0:
        console.print("[bold green]✅ Already up to date — nothing new to download.[/bold green]")
        return

    if not yes and not click.confirm("▶  Start download?", default=True):
        console.print("[yellow]Aborted.[/yellow]")
        return

    chan_dir.mkdir(parents=True, exist_ok=True)
    errlog = _ErrorLog()
    downloaded = []

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
                progress.update(task, advance=1,
                                description=f"[green]✓ {(info_d.get('title') or '?')[:55]}")

        try:
            for tab in tab_entries:
                target = chan_dir / tab if len(tab_entries) > 1 else chan_dir
                target.mkdir(parents=True, exist_ok=True)
                tmpl = os.path.join(
                    str(target).replace("%", "%%"),
                    "%(upload_date>%Y-%m-%d)s - %(title).150s [%(id)s].%(ext)s",
                )

                opts = build_ydl_opts(output, quality, True, 1, limit)
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
                if since:
                    opts["dateafter"] = since
                    opts["breakonreject"] = True         # newest-first, so stop at the cutoff

                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([f"{base}/{tab}"])
        except KeyboardInterrupt:
            console.print("\n[yellow]⏸  Interrupted — re-run the same command to continue "
                          "(finished videos are skipped).[/yellow]")

    # ── Result summary ────────────────────────────────────────────────────────
    failed = errlog.errors
    console.print(
        f"\n[bold green]✅ Done![/bold green]  "
        f"[green]{len(downloaded)}[/green] downloaded  •  "
        f"[red]{len(failed)}[/red] errors"
    )
    for msg in failed[:5]:
        console.print(f"  [red]✗[/red] {msg[:110]}")
    if len(failed) > 5:
        console.print(f"  [dim]… and {len(failed) - 5} more[/dim]")

    save_history({
        "date":       datetime.now().strftime("%Y-%m-%d %H:%M"),
        "url":        url,
        "playlist":   f"{channel_name} (channel)",
        "total":      found,
        "downloaded": len(downloaded),
        "failed":     len(failed),
        "output":     str(chan_dir),
        "quality":    quality,
    })

    console.print(f"\n📁  Saved to: [cyan]{chan_dir}[/cyan]")
    console.print(f"    (Open in Explorer:  [dim]explorer \"{chan_dir}\"[/dim])")


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
