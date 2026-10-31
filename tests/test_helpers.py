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
