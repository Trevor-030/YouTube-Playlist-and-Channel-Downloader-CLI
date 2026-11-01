"""Where files go and what they are called: playlist folders, old/new naming, id renames, URL files, retry passes."""

import os
import re
import time
import yt_dlp

from pathlib import Path

from ytdl.common import console
from ytdl.repair import find_incomplete


def read_archive_ids(archive: Path) -> set:
    if not archive.exists():
        return set()
    return {line.split()[-1] for line in archive.read_text(encoding="utf-8").splitlines() if line.strip()}


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
