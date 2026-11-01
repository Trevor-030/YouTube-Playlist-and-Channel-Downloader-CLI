"""Offline tests for the repair, retry and layout helpers. Run:  pip install pytest  &&  pytest"""
import shutil
import subprocess
from pathlib import Path

import pytest

import yt_downloader as y

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe not installed")


def make_video(path: Path, seconds=2):
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"testsrc=d={seconds}:s=160x120:r=10",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)


def make_audio(path: Path, codec="aac", seconds=2):
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=d={seconds}",
                    "-c:a", codec, str(path)], check=True)


# ── finding leftovers ─────────────────────────────────────────────────────────

def test_find_split_files_matches_variants(tmp_path):
    for n in ("a.f137.mp4", "a.f140.m4a", "b.f251-drc.webm", "c.f140-5.m4a"):
        (tmp_path / n).write_text("x")
    (tmp_path / "Intro.final.mp4").write_text("x")          # normal title, must not match
    (tmp_path / "d.f137.mp4.part").write_text("x")          # incomplete, handled separately
    groups = y.find_split_files(tmp_path)
    assert {g.name for g in groups} == {"a", "b", "c"}
    assert len(groups[tmp_path / "a"]) == 2


def test_find_incomplete(tmp_path):
    for n in ("a.f137.mp4.part", "b.ytdl", "ok.mp4"):
        (tmp_path / n).write_text("x")
    assert {p.name for p in y.find_incomplete(tmp_path)} == {"a.f137.mp4.part", "b.ytdl"}


# ── merging ───────────────────────────────────────────────────────────────────

@needs_ffmpeg
def test_merge_with_emoji_name(tmp_path):
    make_video(tmp_path / "050 - Learn 🎭 [abc].f137.mp4")
    make_audio(tmp_path / "050 - Learn 🎭 [abc].f140.m4a")
    (base, parts), = y.find_split_files(tmp_path).items()
    assert y.merge_group(base, parts)[0] == "merged"
    assert [p.name for p in tmp_path.iterdir()] == ["050 - Learn 🎭 [abc].mp4"]


@needs_ffmpeg
def test_merge_opus_into_mp4(tmp_path):
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=d=2:s=160x120:r=10",
                    "-c:v", "libvpx-vp9", str(tmp_path / "v.f248.webm")], check=True)
    make_audio(tmp_path / "v.f251.webm", codec="libopus")
    (base, parts), = y.find_split_files(tmp_path).items()
    assert y.merge_group(base, parts)[0] == "merged"
    assert (tmp_path / "v.mp4").stat().st_size > 0


@needs_ffmpeg
def test_lone_part_is_skipped_and_names_restored(tmp_path):
    make_video(tmp_path / "x.f137.mp4")
    (base, parts), = y.find_split_files(tmp_path).items()
    assert y.merge_group(base, parts)[0] == "skipped"
    assert [p.name for p in tmp_path.iterdir()] == ["x.f137.mp4"]


@needs_ffmpeg
def test_existing_target_is_not_overwritten(tmp_path):
    make_video(tmp_path / "x.f137.mp4")
    make_audio(tmp_path / "x.f140.m4a")
    (tmp_path / "x.mp4").write_text("already here")
    (base, parts), = y.find_split_files(tmp_path).items()
    assert y.merge_group(base, parts)[0] == "exists"
    assert (tmp_path / "x.mp4").read_text() == "already here"


# ── truncation check ──────────────────────────────────────────────────────────

@needs_ffmpeg
def test_looks_truncated(tmp_path):
    f = tmp_path / "Learn 🎭 [abc].mp4"
    make_video(f, seconds=10)
    assert y.media_duration(f) == pytest.approx(10, abs=0.5)
    assert not y.looks_truncated(f, 10)
    assert y.looks_truncated(f, 60)
    assert not y.looks_truncated(f, None)
    assert [p.name for p in tmp_path.iterdir()] == [f.name]   # probe link cleaned up


# ── layout, legacy names, retries ─────────────────────────────────────────────

def test_new_names_carry_video_id():
    assert "[%(id)s]" in y.build_ydl_opts("/o", "best", True, 1, 0)["outtmpl"]
    assert "[%(id)s]" not in y.build_ydl_opts("/o", "best", True, 1, 0, id_in_name=False)["outtmpl"]


def test_has_legacy_names(tmp_path):
    assert not y.has_legacy_names(tmp_path)
    (tmp_path / "001 - New [dQw4w9WgXcQ].mp4").write_text("x")
    assert not y.has_legacy_names(tmp_path)
    (tmp_path / "002 - Old.mp4").write_text("x")
    assert y.has_legacy_names(tmp_path)


def test_run_with_retries_stops_when_parts_gone(tmp_path, monkeypatch):
    monkeypatch.setattr(y.time, "sleep", lambda s: None)
    part = tmp_path / "a.f140.m4a.part"
    part.write_text("x")
    calls = []

    def one_pass():
        calls.append(1)
        if len(calls) == 3:
            part.unlink()

    class Log:
        errors = ["old"]

    y.run_with_retries(one_pass, tmp_path, 5, Log)
    assert len(calls) == 3 and Log.errors == []


def test_run_with_retries_respects_limit_and_off(tmp_path, monkeypatch):
    monkeypatch.setattr(y.time, "sleep", lambda s: None)
    (tmp_path / "a.f140.m4a.part").write_text("x")
    calls = []

    class Log:
        errors = []

    y.run_with_retries(lambda: calls.append(1), tmp_path, 2, Log)
    assert len(calls) == 3            # first pass + 2 retries
    calls.clear()
    y.run_with_retries(lambda: calls.append(1), tmp_path, 0, Log)
    assert len(calls) == 1


# ── audio-only mode ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("codec,fmt", [("mp3", "bestaudio/best"), ("m4a", "bestaudio[ext=m4a]/bestaudio/best")])
def test_audio_opts(codec, fmt):
    o = y.build_ydl_opts("/o", "best", True, 1, 0, audio=codec)
    assert o["format"] == fmt
    assert o["postprocessors"][0]["preferredcodec"] == codec
    assert "merge_output_format" not in o and "format_sort" not in o
    assert "(audio)" in o["outtmpl"]               # own folder, own archive


def test_video_opts_unchanged_by_audio_feature():
    o = y.build_ydl_opts("/o", "best", True, 1, 0)
    assert "postprocessors" not in o and o["merge_output_format"] == "mp4"
    assert "(audio)" not in o["outtmpl"]


# ── subtitles ─────────────────────────────────────────────────────────────────

def test_parse_langs():
    assert y.parse_langs("en, fr") == ["en", "fr"]
    assert y.parse_langs(None) is None and y.parse_langs(" , ") is None


def test_subs_opts():
    o = y.build_ydl_opts("/o", "best", True, 1, 0, subs=["en"])
    assert o["writesubtitles"] and o["writeautomaticsub"] and o["subtitleslangs"] == ["en"]
    assert {"key": "FFmpegSubtitlesConvertor", "format": "srt", "when": "before_dl"} in o["postprocessors"]
    assert not y.build_ydl_opts("/o", "best", True, 1, 0, subs=["all"])["writeautomaticsub"]
    assert "writesubtitles" not in y.build_ydl_opts("/o", "best", True, 1, 0)


def test_subs_work_with_audio_mode():
    keys = [p["key"] for p in y.build_ydl_opts("/o", "best", True, 1, 0, audio="mp3", subs=["en"])["postprocessors"]]
    assert keys == ["FFmpegExtractAudio", "FFmpegSubtitlesConvertor"]


def test_subtitle_files_are_not_mistaken_for_leftovers(tmp_path):
    (tmp_path / "001 - Intro [dQw4w9WgXcQ].en.srt").write_text("x")
    assert y.find_split_files(tmp_path) == {} and y.find_incomplete(tmp_path) == []
    assert not y.has_legacy_names(tmp_path)


# ── batch file ────────────────────────────────────────────────────────────────

def test_read_url_file(tmp_path):
    f = tmp_path / "urls.txt"
    f.write_text("# queue\nhttps://youtube.com/playlist?list=A  # java\n\nhttps://youtube.com/playlist?list=A\n"
                 "oops\nhttps://youtube.com/@chan\n", encoding="utf-8")
    urls, bad = y.read_url_file(f)
    assert urls == ["https://youtube.com/playlist?list=A", "https://youtube.com/@chan"]
    assert bad == [(5, "oops")]


def _run_batch(tmp_path, monkeypatch, behaviour):
    """Run `yt batch` with a fake download command; behaviour(url) returns a status or raises."""
    from click.testing import CliRunner
    f = tmp_path / "urls.txt"
    f.write_text("\n".join(f"https://youtube.com/playlist?list={n}" for n in ("A", "B", "C")))
    monkeypatch.setattr(y, "require_ffmpeg", lambda: None)
    monkeypatch.setattr(y, "HISTORY_FILE", tmp_path / "history.json")
    seen = []

    def fake(**kw):
        seen.append(kw["url"][-1])
        assert kw["yes"] is True                     # batch must never prompt
        return behaviour(kw["url"][-1])

    monkeypatch.setattr(y.download, "callback", fake)
    res = CliRunner().invoke(y.cli, ["batch", str(f)])
    return res, seen


def test_batch_continues_after_failures(tmp_path, monkeypatch):
    def behaviour(n):
        if n == "A":
            raise RuntimeError("boom")
        if n == "B":
            raise SystemExit(1)
        return "uptodate"

    res, seen = _run_batch(tmp_path, monkeypatch, behaviour)
    assert seen == ["A", "B", "C"] and res.exit_code == 0
    assert "boom" in res.output and "already up to date" in res.output


def test_batch_stops_cleanly_on_ctrl_c(tmp_path, monkeypatch):
    def behaviour(n):
        if n == "B":
            raise KeyboardInterrupt
        return "uptodate"

    res, seen = _run_batch(tmp_path, monkeypatch, behaviour)
    assert seen == ["A", "B"] and "Batch stopped" in res.output
    assert "1 URL(s) not started" in res.output


# ── history never breaks a run ────────────────────────────────────────────────

def test_damaged_history_is_ignored(tmp_path, monkeypatch):
    hist = tmp_path / "sub" / "history.json"
    monkeypatch.setattr(y, "HISTORY_FILE", hist)
    monkeypatch.setattr(y, "APP_DIR", hist.parent)
    assert y.load_history() == []                    # missing file
    hist.parent.mkdir()
    hist.write_text("{not json")
    assert y.load_history() == []                    # damaged file
    y.save_history({"a": 1})                         # overwrites the damage
    assert y.load_history() == [{"a": 1}]


# ── embed cover art / chapters / tags ─────────────────────────────────────────

def test_embed_opts_order_and_flags():
    o = y.build_ydl_opts("/o", "best", True, 1, 0, embed=True)
    keys = [p["key"] for p in o["postprocessors"]]
    assert keys == ["FFmpegMetadata", "FFmpegThumbnailsConvertor", "EmbedThumbnail"]   # metadata before thumbnail
    assert o["writethumbnail"] is True
    assert o["postprocessors"][0]["add_chapters"] is True


def test_embed_off_by_default():
    o = y.build_ydl_opts("/o", "best", True, 1, 0)
    assert "postprocessors" not in o and "writethumbnail" not in o


def test_embed_after_audio_extraction():
    keys = [p["key"] for p in y.build_ydl_opts("/o", "best", True, 1, 0, audio="m4a", subs=["en"], embed=True)["postprocessors"]]
    assert keys[0] == "FFmpegExtractAudio"                     # convert first, then tag the result
    assert keys[-1] == "EmbedThumbnail"
    assert keys.index("FFmpegMetadata") < keys.index("EmbedThumbnail")


def test_embed_without_mutagen_keeps_chapters_and_tags(monkeypatch):
    real = y.importlib.util.find_spec
    monkeypatch.setattr(y.importlib.util, "find_spec", lambda n: None if n == "mutagen" else real(n))
    assert not y.can_embed_thumbnail(None) and y.can_embed_thumbnail("mp3")   # mp3 only needs ffmpeg
    o = y.build_ydl_opts("/o", "best", True, 1, 0, embed=True)
    assert [p["key"] for p in o["postprocessors"]] == ["FFmpegMetadata"] and "writethumbnail" not in o


@pytest.mark.parametrize("cmd", ["download", "channel", "batch"])
def test_embed_flag_exists_on_every_download_command(cmd):
    assert any(p.name == "embed" for p in y.cli.commands[cmd].params)


@needs_ffmpeg
def test_embed_really_writes_chapters_tags_and_cover(tmp_path):
    pytest.importorskip("mutagen")
    import json
    mp4, jpg = tmp_path / "v [abc].mp4", tmp_path / "v [abc].jpg"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=d=4:s=160x120:r=10", "-f", "lavfi",
                    "-i", "sine=d=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(mp4)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=160x90", "-frames:v", "1", str(jpg)], check=True)
    opts = {**y.build_ydl_opts(str(tmp_path), "best", True, 1, 0, embed=True), "quiet": True}
    info = {"id": "abc", "title": "Lesson", "uploader": "Prof", "ext": "mp4", "filepath": str(mp4),
            "chapters": [{"start_time": 0, "end_time": 2, "title": "A"}, {"start_time": 2, "end_time": 4, "title": "B"}],
            "thumbnails": [{"id": "0", "url": "https://x.test/t.jpg", "filepath": str(jpg)}]}
    y.yt_dlp.YoutubeDL(opts).post_process(str(mp4), info, {str(mp4): str(mp4)})
    out = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format_tags=title:stream=codec_name:stream_disposition=attached_pic:chapter=id",
         "-of", "json", str(mp4)], capture_output=True, text=True).stdout)
    assert len(out["chapters"]) == 2 and out["format"]["tags"]["title"] == "Lesson"
    assert any(s.get("disposition", {}).get("attached_pic") == 1 for s in out["streams"])
    assert not jpg.exists()                                   # loose image cleaned up


# ── speed limit ───────────────────────────────────────────────────────────────

def test_parse_rate():
    assert y.parse_rate(None, None, "2M") == 2 * 1024 * 1024
    assert y.parse_rate(None, None, "500K") == 500 * 1024
    assert y.parse_rate(None, None, None) is None
    import click
    for bad in ("abc", "0", "10"):
        with pytest.raises(click.BadParameter):
            y.parse_rate(None, None, bad)


def test_rate_limit_uses_single_connection():
    o = y.build_ydl_opts("/o", "best", True, 1, 0, rate_limit=1_000_000)
    assert o["ratelimit"] == 1_000_000 and o["concurrent_fragment_downloads"] == 1
    o = y.build_ydl_opts("/o", "best", True, 1, 0)
    assert "ratelimit" not in o and o["concurrent_fragment_downloads"] == 4


def test_rate_limit_reaches_yt_dlp_and_bad_value_is_rejected(monkeypatch, tmp_path):
    from click.testing import CliRunner
    monkeypatch.setattr(y, "require_ffmpeg", lambda: None)
    monkeypatch.setattr(y, "HISTORY_FILE", tmp_path / "h.json")
    monkeypatch.setattr(y, "fetch_playlist_info", lambda u: {"title": "L", "entries": [{"id": "aaaaaaaaaaa"}]})
    seen = {}

    class Fake(y.yt_dlp.YoutubeDL):
        def download(self, urls):
            seen["rate"] = self.params.get("ratelimit")

    monkeypatch.setattr(y.yt_dlp, "YoutubeDL", Fake)
    r = CliRunner().invoke(y.cli, ["download", "https://youtube.com/playlist?list=x", "-o", str(tmp_path),
                                   "--rate-limit", "2M", "-y"])
    assert r.exit_code == 0 and seen["rate"] == 2 * 1024 * 1024
    bad = CliRunner().invoke(y.cli, ["download", "https://youtube.com/playlist?list=x", "--rate-limit", "fast"])
    assert bad.exit_code == 2 and "500K or 2M" in bad.output


# ── notifications ─────────────────────────────────────────────────────────────

def _spy_run(monkeypatch):
    calls = []
    monkeypatch.setattr(y.subprocess, "run", lambda *a, **k: calls.append((a[0], k)))
    return calls


def test_notify_windows_passes_text_via_env(monkeypatch):
    import sys, types
    calls = _spy_run(monkeypatch)
    monkeypatch.setattr(y.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winsound", types.SimpleNamespace(MessageBeep=lambda: None))
    y.notify('Done "quoted" 🎬', "5 new")
    cmd, kw = calls[0]
    assert cmd[0] == "powershell" and "$env:YT_TITLE" in cmd[-1]
    assert kw["env"]["YT_TITLE"] == 'Done "quoted" 🎬'          # text never touches the command line


def test_notify_mac_and_linux(monkeypatch):
    calls = _spy_run(monkeypatch)
    monkeypatch.setattr(y.sys, "platform", "darwin")
    y.notify("T", "B")
    assert calls[-1][0][0] == "osascript" and calls[-1][0][-2:] == ["T", "B"]
    monkeypatch.setattr(y.sys, "platform", "linux")
    monkeypatch.setattr(y.shutil, "which", lambda n: "/usr/bin/notify-send")
    y.notify("T", "B")
    assert calls[-1][0] == ["notify-send", "T", "B"]


def test_notify_never_raises(monkeypatch, capsys):
    def boom(*a, **k):
        raise OSError("no shell")
    monkeypatch.setattr(y.subprocess, "run", boom)
    monkeypatch.setattr(y.sys, "platform", "darwin")
    y.notify("T", "B")                                           # must not raise
    assert "\a" in capsys.readouterr().out                       # falls back to a beep


def test_batch_sends_one_notification_at_the_end(tmp_path, monkeypatch):
    from click.testing import CliRunner
    f = tmp_path / "urls.txt"
    f.write_text("https://youtube.com/playlist?list=A\nhttps://youtube.com/playlist?list=B\n")
    monkeypatch.setattr(y, "require_ffmpeg", lambda: None)
    monkeypatch.setattr(y, "HISTORY_FILE", tmp_path / "h.json")
    sent, inner = [], []
    monkeypatch.setattr(y, "notify", lambda t, b: sent.append((t, b)))
    monkeypatch.setattr(y.download, "callback", lambda **kw: inner.append(kw["notify_when_done"]) or "uptodate")
    r = CliRunner().invoke(y.cli, ["batch", str(f), "--notify"])
    assert r.exit_code == 0 and inner == [False, False]          # per-URL runs stay quiet
    assert len(sent) == 1 and "2 URL(s)" in sent[0][1]


@pytest.mark.parametrize("cmd", ["download", "channel", "batch"])
def test_speed_and_notify_flags_exist(cmd):
    names = {p.name for p in y.cli.commands[cmd].params}
    assert {"rate_limit", "notify_when_done"} <= names


# ── add-ids (rename old files to carry the video id) ──────────────────────────

def _stem(i, e):
    return f"{i:03d} - {e['title']}"


def _entries():
    return [{"id": "aaaaaaaaaaa", "title": "One"}, {"id": "bbbbbbbbbbb", "title": "Two"},
            None, {"id": "ddddddddddd", "title": "Four"}]


def test_plan_renames_all_sidecar_files(tmp_path):
    for n in ("001 - One.mp4", "001 - One.en.srt", "001 - One.f137.mp4.part"):
        (tmp_path / n).write_text("x")
    plan = y.plan_id_renames(tmp_path, _entries(), _stem)
    assert {n.name for _, n in plan["rename"]} == {
        "001 - One [aaaaaaaaaaa].mp4", "001 - One [aaaaaaaaaaa].en.srt", "001 - One [aaaaaaaaaaa].f137.mp4.part"}


def test_plan_skips_done_missing_and_none_entries(tmp_path):
    (tmp_path / "002 - Two [bbbbbbbbbbb].mp4").write_text("x")       # already tagged
    plan = y.plan_id_renames(tmp_path, _entries(), _stem)            # 003 is None, 004 not downloaded
    assert plan["rename"] == [] and plan["done"] == 1 and plan["mismatched"] == []


def test_plan_never_renames_a_file_with_a_different_title(tmp_path):
    (tmp_path / "001 - Something else.mp4").write_text("x")
    (tmp_path / "099 - Orphan.mp4").write_text("x")
    plan = y.plan_id_renames(tmp_path, _entries(), _stem)
    assert plan["rename"] == []
    assert plan["mismatched"] == [(1, "One", "001 - Something else.mp4")]
    assert [f.name for f in plan["orphans"]] == ["099 - Orphan.mp4"]


def test_plan_does_not_overwrite_or_exceed_path_limit(tmp_path, monkeypatch):
    (tmp_path / "001 - One.mp4").write_text("x")
    (tmp_path / "001 - One [aaaaaaaaaaa].mp4").write_text("already here")
    plan = y.plan_id_renames(tmp_path, _entries(), _stem)
    assert plan["rename"] == [] and "already exists" in plan["skipped"][0][1]
    (tmp_path / "001 - One [aaaaaaaaaaa].mp4").unlink()
    monkeypatch.setattr(y, "MAX_PATH", 10)
    assert "too long" in y.plan_id_renames(tmp_path, _entries(), _stem)["skipped"][0][1]


def _run_add_ids(tmp_path, monkeypatch, *flags):
    """Run `yt add-ids` against a folder named exactly like yt-dlp's old naming would make it."""
    from click.testing import CliRunner
    ents = [{"id": "aaaaaaaaaaa", "title": "Intro ☕ v1.2: what/why?"}, {"id": "bbbbbbbbbbb", "title": "x" * 150}]
    monkeypatch.setattr(y, "fetch_playlist_info", lambda u: {"title": "My List ☕", "entries": ents})
    opts = y.build_ydl_opts(str(tmp_path), "best", True, 1, 0, id_in_name=False)
    ydl = y.yt_dlp.YoutubeDL({**opts, "quiet": True})
    names = []
    for i, e in enumerate(ents, 1):
        p = Path(ydl.prepare_filename({"id": e["id"], "title": e["title"], "ext": "mp4",
                                       "playlist_title": "My List ☕", "playlist_index": i}))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
        names.append(p)
    res = CliRunner().invoke(y.cli, ["add-ids", "https://youtube.com/playlist?list=x", "-o", str(tmp_path), *flags])
    return res, names


def test_add_ids_dry_run_changes_nothing(tmp_path, monkeypatch):
    res, names = _run_add_ids(tmp_path, monkeypatch)
    assert res.exit_code == 0 and "Dry run" in res.output
    assert all(p.exists() for p in names)


def test_add_ids_apply_handles_awkward_titles_and_is_repeatable(tmp_path, monkeypatch):
    from click.testing import CliRunner
    res, names = _run_add_ids(tmp_path, monkeypatch, "--apply")
    assert res.exit_code == 0 and "Renamed 2 file(s)" in res.output
    left = sorted(p.name for p in names[0].parent.iterdir())
    assert any("[aaaaaaaaaaa].mp4" in n for n in left) and any("[bbbbbbbbbbb].mp4" in n for n in left)
    assert not y.has_legacy_names(names[0].parent)                  # new downloads now use the new naming too
    again = CliRunner().invoke(y.cli, ["add-ids", "https://youtube.com/playlist?list=x", "-o", str(tmp_path), "--apply"])
    assert "Nothing to rename" in again.output
