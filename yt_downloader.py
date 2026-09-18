#!/usr/bin/env python3
"""
YouTube Playlist Downloader CLI  —  Windows Edition
-----------------------------------------------------
Requirements:
    pip install yt-dlp rich click
    ffmpeg must be installed and on PATH (see README)
"""

import os
import json
import sys
import shutil
import click
from datetime import datetime
from pathlib import Path, WindowsPath

try:
    import yt_dlp
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
def download(url, output, quality, resume, start, end):
    """Download a YouTube playlist or single video."""

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


# ── info ──────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("url")
def info(url):
    """Preview playlist contents without downloading."""
    console.print("[yellow]⏳ Fetching info…[/yellow]")
    data = fetch_playlist_info(url)

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
