#!/usr/bin/env python3
"""
YouTube Playlist & Channel Downloader CLI  —  Windows Edition
-----------------------------------------------------
Requirements:
    pip install "yt-dlp[default]" rich click
    ffmpeg must be installed and on PATH (see README)

This file is the command line (the `yt` command). The building blocks are in the ytdl/ folder next to it.
"""

import click
import os
import re
import subprocess
import sys
import yt_dlp

from datetime import datetime
from pathlib import Path
from rich import box
from rich.markup import escape
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from yt_dlp.utils import sanitize_filename
from yt_dlp.version import __version__ as YTDLP_VERSION

from ytdl.checks import looks_truncated, report_suspect, warn_if_no_js_runtime, warn_if_old_ytdlp
from ytdl.common import _ErrorLog, clean_msg, console, require_ffmpeg, safe_win_path
from ytdl.config import CONFIG_FILE, DEFAULT_OUTPUT, HISTORY_FILE, load_config, load_history, save_config, save_history
from ytdl.layout import AUTO_PASSES, has_legacy_names, plan_id_renames, playlist_folder, read_archive_ids, read_url_file, run_with_retries
from ytdl.notifications import notify
from ytdl.options import AUDIO_FORMATS, QUALITY_OPTIONS, build_ydl_opts, parse_langs, parse_rate, warn_embed
from ytdl.repair import repair_folder
from ytdl.sources import CHANNEL_TABS, channel_base, fetch_playlist_info, is_channel_url, resolve_url


@click.group()
def cli():
    """🎬  YouTube Playlist Downloader  (Windows Edition)"""
    pass


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


@cli.command("notify-test")
def notify_test():
    """Send a test notification, to check that --notify works on this computer."""
    console.print("[yellow]Sending a test notification…[/yellow]")
    notify("YT Downloader", "Notifications work ✅")
    console.print("Did a notification pop up (or did you hear a beep)? If not, --notify is silent here.")


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


if __name__ == "__main__":
    cli()
