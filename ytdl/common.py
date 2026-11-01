"""Shared pieces: the console, the ffmpeg check, path cleanup and the yt-dlp error collector."""

import os
import re
import shutil

from pathlib import Path
from rich.console import Console
from rich.panel import Panel


console = Console()


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


def safe_win_path(p: str) -> str:
    """Expand env vars like %USERPROFILE% and resolve the path."""
    return str(Path(os.path.expandvars(p)).resolve())


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m|\[[0-9;]*m")


def clean_msg(msg: str) -> str:
    return _ANSI_RE.sub("", msg).strip()


class _ErrorLog:
    """yt-dlp logger that stays quiet but remembers errors (private/members-only videos, etc.)."""
    def __init__(self):
        self.errors = []
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): self.errors.append(str(msg))
