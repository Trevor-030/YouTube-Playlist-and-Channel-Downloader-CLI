"""Transcripts: save YouTube captions as text, Markdown, JSON, SRT or VTT (the `yt transcripts` command).

Works on single videos, playlists and whole channels. Re-running is safe: videos that already have
output files are skipped, so an interrupted or blocked run picks up where it stopped.
"""

from __future__ import annotations

import csv
import json
import random
import re
import threading
import time

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yt_dlp

from rich.markup import escape

from ytdl.common import console
from ytdl.sources import _TAB_RE, channel_base, is_channel_url


FORMATS = ("txt", "md", "json", "srt", "vtt")


_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")


_VID_RE = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/|/embed/|/live/|/v/)([A-Za-z0-9_-]{11})")


_UC_RE = re.compile(r"UC[A-Za-z0-9_-]{22}")


@dataclass
class Options:
    out: Path
    langs: list[str] = field(default_factory=lambda: ["en"])
    formats: list[str] = field(default_factory=lambda: ["txt"])
    any_lang: bool = False
    timestamps: bool = False
    shorts: bool = False
    streams: bool = False
    limit: int = 0
    workers: int = 4
    delay: float = 0.5
    retries: int = 3
    proxy: str | None = None
    combine: bool = False
    overwrite: bool = False


# ── what to fetch ─────────────────────────────────────────────────────────────

def video_id_from(target: str) -> str | None:
    """The 11-character video id in a watch/short/embed/youtu.be URL, or a bare id. None otherwise."""
    t = target.strip()
    if _ID_RE.fullmatch(t):
        return t
    m = _VID_RE.search(t)
    return m.group(1) if m else None


def channel_urls(target: str, shorts: bool = False, streams: bool = False) -> list[str]:
    """Turn a channel @handle, UC… id or URL into the tab URL(s) to crawl. Anything else is returned as-is."""
    t = target.strip().rstrip("/")
    if t.startswith("@"):
        t = f"https://www.youtube.com/{t}"
    elif _UC_RE.fullmatch(t):
        t = f"https://www.youtube.com/channel/{t}"
    path = t.split("?")[0].split("#")[0].rstrip("/")
    if not is_channel_url(t) or _TAB_RE.search(path):
        return [t]                    # playlist, explicit tab, or something yt-dlp handles itself
    tabs = ["videos"] + (["shorts"] if shorts else []) + (["streams"] if streams else [])
    return [f"{channel_base(t)}/{tab}" for tab in tabs]


def read_targets(path) -> list[str]:
    """One target per line (URL, @handle, channel id or video id). '#' starts a comment. Duplicates dropped."""
    targets = []
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.split("#", 1)[0].strip()
        if line and line.split()[0] not in targets:
            targets.append(line.split()[0])
    return targets


class _QuietLogger:
    def debug(self, *_): pass
    def warning(self, *_): pass
    def error(self, *_): pass


def _walk(info: dict):
    """Yield the video entries of a (possibly nested) yt-dlp result."""
    if not info:
        return
    if info.get("entries") is not None:
        for e in info["entries"]:
            yield from _walk(e)
    elif _ID_RE.fullmatch(info.get("id") or ""):
        yield info


def enumerate_videos(target: str, opts: Options) -> tuple[list[dict], str | None, bool]:
    """Return (videos, channel or playlist name, is_single_video)."""
    vid = video_id_from(target)
    urls = [f"https://www.youtube.com/watch?v={vid}"] if vid else channel_urls(target, opts.shorts, opts.streams)
    ydl_opts = {
        "quiet": True, "no_warnings": True, "skip_download": True, "ignoreerrors": True,
        "extract_flat": "in_playlist", "logger": _QuietLogger(),
    }
    if opts.limit:
        ydl_opts["playlistend"] = opts.limit
    if opts.proxy:
        ydl_opts["proxy"] = opts.proxy

    videos: dict[str, dict] = {}
    name = None
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for url in urls:
            try:
                info = ydl.extract_info(url, download=False)
            except Exception as exc:
                console.print(f"  [red]Could not read {escape(url)}: {escape(str(exc))}[/red]")
                continue
            if not info:
                continue
            if not vid:
                name = name or info.get("channel") or info.get("uploader") or info.get("title")
            for e in _walk(info):
                videos.setdefault(e["id"], {"id": e["id"], "title": e.get("title") or e["id"]})
    found = list(videos.values())
    if opts.limit:
        found = found[: opts.limit]
    return found, name, bool(vid)


# ── fetching ──────────────────────────────────────────────────────────────────

_local = threading.local()


def get_api(proxy: str | None):
    """One YouTubeTranscriptApi per worker thread (its HTTP session is not thread-safe)."""
    if getattr(_local, "proxy", object()) != proxy:
        from youtube_transcript_api import YouTubeTranscriptApi
        cfg = None
        if proxy:
            from youtube_transcript_api.proxies import GenericProxyConfig
            cfg = GenericProxyConfig(http_url=proxy, https_url=proxy)
        _local.api, _local.proxy = YouTubeTranscriptApi(proxy_config=cfg), proxy
    return _local.api


def classify(exc: Exception) -> str:
    """Map a youtube-transcript-api error to a short status for the index and summary."""
    n = type(exc).__name__
    if n in ("IpBlocked", "RequestBlocked"):
        return "blocked"
    if n == "TranscriptsDisabled":
        return "disabled"
    if n in ("NoTranscriptFound", "NotTranslatable", "TranslationLanguageNotAvailable"):
        return "no_transcript"
    if n in ("VideoUnavailable", "VideoUnplayable", "InvalidVideoId"):
        return "unavailable"
    if n == "AgeRestricted":
        return "age_restricted"
    return "error"


def fetch_transcript(api, vid: str, langs: list[str], any_lang: bool):
    """Return (snippets, language code, is auto-generated). Falls back to any language with any_lang."""
    tl = api.list(vid)
    try:
        tr = tl.find_transcript(langs)
    except Exception as exc:
        if not any_lang or type(exc).__name__ != "NoTranscriptFound":
            raise
        tr = next(iter(tl), None)
        if tr is None:
            raise
    snippets = [{"text": s.text, "start": float(s.start), "duration": float(s.duration)} for s in tr.fetch()]
    return snippets, tr.language_code, bool(tr.is_generated)


# ── writing ───────────────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\n", " ")).strip()


def _stamp(sec: float, sep: str) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _mmss(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _body(snips: list[dict], timestamps: bool) -> str:
    """Plain text: one [mm:ss] line per caption, or captions joined into readable paragraphs."""
    if timestamps:
        return "\n".join(f"[{_mmss(s['start'])}] {_clean(s['text'])}" for s in snips)
    paras, cur, n = [], [], 0
    for s in snips:
        t = _clean(s["text"])
        if not t:
            continue
        cur.append(t)
        n += len(t) + 1
        if (n > 700 and t[-1] in ".?!") or n > 1200:
            paras.append(" ".join(cur))
            cur, n = [], 0
    if cur:
        paras.append(" ".join(cur))
    return "\n\n".join(paras)


def render(fmt: str, meta: dict, snips: list[dict], timestamps: bool = False) -> str:
    if fmt == "txt":
        return _body(snips, timestamps) + "\n"
    if fmt == "md":
        head = (f"# {meta['title']}\n\n- URL: {meta['url']}\n- Language: {meta['language']}"
                f"{' (auto-generated)' if meta['generated'] else ''}\n\n")
        return head + _body(snips, timestamps) + "\n"
    if fmt == "json":
        return json.dumps({**meta, "segments": snips}, ensure_ascii=False, indent=2)
    if fmt == "srt":
        return "\n".join(
            f"{i}\n{_stamp(s['start'], ',')} --> {_stamp(s['start'] + s['duration'], ',')}\n{_clean(s['text'])}\n"
            for i, s in enumerate(snips, 1))
    if fmt == "vtt":
        cues = "\n".join(
            f"{_stamp(s['start'], '.')} --> {_stamp(s['start'] + s['duration'], '.')}\n{_clean(s['text'])}\n"
            for s in snips)
        return "WEBVTT\n\n" + cues
    raise ValueError(f"unknown format: {fmt}")


def slug(s: str, n: int = 80) -> str:
    """A title that is safe as a Windows file name, cut to n characters."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", s or "")
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:n].rstrip(" .") or "video"


# ── one video ─────────────────────────────────────────────────────────────────

@dataclass
class Result:
    video_id: str
    title: str
    url: str
    status: str
    language: str = ""
    generated: str = ""
    file: str = ""
    detail: str = ""


def process(v: dict, folder: Path, opts: Options, abort: threading.Event) -> Result:
    """Fetch one transcript and write every requested format. Retries blocks and transient errors."""
    vid, title = v["id"], v["title"]
    url = f"https://www.youtube.com/watch?v={vid}"
    kind, detail = "skipped", "stopped after YouTube blocked this IP"

    for attempt in range(opts.retries + 1):
        if abort.is_set():
            break
        time.sleep(random.uniform(opts.delay * 0.5, opts.delay * 1.5))
        try:
            snips, lang, gen = fetch_transcript(get_api(opts.proxy), vid, opts.langs, opts.any_lang)
            base = folder / f"{slug(title)}__{vid}"
            meta = {"video_id": vid, "title": title, "url": url, "language": lang, "generated": gen}
            for fmt in opts.formats:
                Path(f"{base}.{fmt}").write_text(render(fmt, meta, snips, opts.timestamps), encoding="utf-8")
            return Result(vid, title, url, "ok", lang, "yes" if gen else "no", f"{base}.{opts.formats[0]}")
        except Exception as exc:
            kind = classify(exc)
            detail = next((ln.strip() for ln in str(exc).splitlines() if ln.strip()), type(exc).__name__)[:200]
            if kind in ("blocked", "error") and attempt < opts.retries:
                time.sleep(min(60, 5 * 2 ** attempt) + random.random() * 2)
                continue
            break

    if kind == "blocked":
        abort.set()                   # everyone stops: hammering a blocked IP only extends the block
    return Result(vid, title, url, kind, detail=detail)


# ── a whole target ────────────────────────────────────────────────────────────

def load_index(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        return {r["video_id"]: r for r in csv.DictReader(f)}


def write_index(path: Path, rows: list[Result]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(Result.__dataclass_fields__))
        w.writeheader()
        for r in rows:
            w.writerow(asdict(r))


def _done_ids(folder: Path, formats: list[str]) -> set[str]:
    """Video ids that already have a file for every requested format."""
    sets = [{p.stem.rsplit("__", 1)[-1] for p in folder.glob(f"*.{f}")} for f in formats]
    return set.intersection(*sets) if sets else set()


def run_target(target: str, opts: Options) -> tuple[bool, dict[str, int]]:
    """Fetch every transcript for one target. Returns (finished without being blocked, status counts)."""
    console.print(f"\n[bold cyan]== {escape(target)}[/bold cyan]")
    videos, name, single = enumerate_videos(target, opts)
    if not videos:
        console.print("  [yellow]No videos found (check the URL, or try --proxy if YouTube is blocking you).[/yellow]")
        return True, {}
    folder = Path(opts.out) if single else Path(opts.out) / slug(name or "channel")
    folder.mkdir(parents=True, exist_ok=True)
    console.print(f"  {len(videos)} video(s) → [dim]{escape(str(folder))}[/dim]")

    prev = load_index(folder / "index.csv")
    done = set() if opts.overwrite else _done_ids(folder, opts.formats)
    existing = {p.stem.rsplit("__", 1)[-1]: str(p) for p in folder.glob(f"*.{opts.formats[0]}")}

    results: dict[str, Result] = {}
    todo = []
    for v in videos:
        if v["id"] in done:
            p = prev.get(v["id"], {})
            results[v["id"]] = Result(v["id"], v["title"], f"https://www.youtube.com/watch?v={v['id']}", "cached",
                                      p.get("language", ""), p.get("generated", ""), existing.get(v["id"], ""))
        else:
            todo.append(v)
    if results:
        console.print(f"  {len(results)} already saved (skipped), {len(todo)} to fetch")

    abort, n = threading.Event(), 0
    ex = ThreadPoolExecutor(max_workers=max(1, opts.workers))
    try:
        futs = [ex.submit(process, v, folder, opts, abort) for v in todo]
        for fut in as_completed(futs):
            r = fut.result()
            results[r.video_id] = r
            n += 1
            if r.status == "ok":
                colour, tail = "green", f" [dim]({r.language}{', auto' if r.generated == 'yes' else ''})[/dim]"
            else:
                colour, tail = "yellow", f" [dim]- {escape(r.detail)}[/dim]"
            console.print(f"  [{colour}][{n}/{len(todo)}] {r.status:<14}[/{colour}] {escape(r.title[:60])}{tail}")
    except KeyboardInterrupt:
        console.print("\n  [yellow]Interrupted - saving progress…[/yellow]")
        abort.set()
        ex.shutdown(wait=False, cancel_futures=True)
    finally:
        ex.shutdown(wait=True, cancel_futures=True)
        ordered = [results[v["id"]] for v in videos if v["id"] in results]
        write_index(folder / "index.csv", ordered)

    counts: dict[str, int] = {}
    for r in ordered:
        counts[r.status] = counts.get(r.status, 0) + 1
    console.print("  Summary: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    if opts.combine:
        parts = []
        for r in ordered:
            if r.status in ("ok", "cached"):
                md = next(iter(folder.glob(f"*__{r.video_id}.md")), None)
                if md:
                    parts.append(md.read_text(encoding="utf-8"))
        combined = folder / "_all_transcripts.md"
        combined.write_text("\n\n---\n\n".join(parts), encoding="utf-8")
        console.print(f"  Combined {len(parts)} transcripts → [dim]{escape(str(combined))}[/dim]")

    if abort.is_set() and any(r.status in ("blocked", "skipped") for r in ordered):
        console.print("  [red]YouTube blocked this IP.[/red] Wait a while, use fewer --workers or a bigger --delay,"
                      " or try --proxy.\n  Re-run the same command to continue where it stopped.")
        return False, counts
    return True, counts
