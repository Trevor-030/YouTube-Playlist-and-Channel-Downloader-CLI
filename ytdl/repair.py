"""Finding and merging video/audio files that ffmpeg failed to merge, and spotting half-finished downloads."""

import re
import shutil
import subprocess

from pathlib import Path
from rich.markup import escape

from ytdl.common import console


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
