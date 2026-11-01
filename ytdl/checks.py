"""Health checks: old yt-dlp, missing JS runtime, files that look cut short."""

import os
import shutil
import subprocess
import yt_dlp

from datetime import datetime
from pathlib import Path
from rich.markup import escape
from rich.panel import Panel
from yt_dlp.version import __version__ as YTDLP_VERSION

from ytdl.common import console


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


def report_suspect(suspect: list):
    if not suspect:
        return
    console.print(f"\n[yellow]⚠ {len(suspect)} file(s) are shorter than YouTube says — may be cut off:[/yellow]")
    for n in suspect[:5]:
        console.print(f"  [dim]{escape(n[:90])}[/dim]")
    if len(suspect) > 5:
        console.print(f"  [dim]… and {len(suspect) - 5} more[/dim]")
    console.print("  Delete the file and its line in downloaded.txt, then re-run the same command.")
