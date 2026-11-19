"""Offline tests for `yt transcripts`: a fake transcript API stands in for YouTube. Run:  pytest"""
import csv
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

import yt_downloader as y
from ytdl import transcripts as tx


# ── fakes ─────────────────────────────────────────────────────────────────────

def _error(name):
    """An exception whose class name matches a youtube-transcript-api error (classify() goes by name)."""
    return type(name, (Exception,), {})(f"{name}: details here\nmore lines")


class FakeTranscript:
    def __init__(self, lang="en", generated=False, lines=("Hello there.", "General Kenobi!")):
        self.language_code, self.is_generated, self._lines = lang, generated, lines

    def fetch(self):
        return [SimpleNamespace(text=t, start=i * 2.5, duration=2.0) for i, t in enumerate(self._lines)]


class FakeList:
    def __init__(self, transcripts):
        self._t = transcripts

    def find_transcript(self, langs):
        for t in self._t:
            if t.language_code in langs:
                return t
        raise _error("NoTranscriptFound")

    def __iter__(self):
        return iter(self._t)


class FakeApi:
    """Per video id: a list of FakeTranscript, or an exception to raise.
    A tuple of those is one outcome per call, and the last one repeats."""
    def __init__(self, plan=None):
        self.plan, self.calls = plan or {}, []

    def list(self, vid):
        self.calls.append(vid)
        p = self.plan.get(vid, [FakeTranscript()])
        if isinstance(p, tuple):
            p = p[min(self.calls.count(vid), len(p)) - 1]
        if isinstance(p, Exception):
            raise p
        return FakeList(p)


@pytest.fixture
def api(monkeypatch):
    fake = FakeApi()
    monkeypatch.setattr(tx, "get_api", lambda proxy: fake)
    monkeypatch.setattr(tx.time, "sleep", lambda s: None)
    return fake


def fake_videos(monkeypatch, videos, name="My Channel", single=False):
    monkeypatch.setattr(tx, "enumerate_videos", lambda target, opts: (list(videos), name, single))


VIDS = [{"id": "aaaaaaaaaaa", "title": "First: intro?"}, {"id": "bbbbbbbbbbb", "title": "Second"}]


# ── targets ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("target, expected", [
    ("dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL123", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ?t=10", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/playlist?list=PLabc", None),
    ("@mkbhd", None),
])
def test_video_id_from(target, expected):
    assert tx.video_id_from(target) == expected


def test_channel_urls_expands_handles_ids_and_tabs():
    assert tx.channel_urls("@mkbhd") == ["https://www.youtube.com/@mkbhd/videos"]
    assert tx.channel_urls("https://www.youtube.com/@mkbhd/?si=x", shorts=True, streams=True) == [
        "https://www.youtube.com/@mkbhd/videos",
        "https://www.youtube.com/@mkbhd/shorts",
        "https://www.youtube.com/@mkbhd/streams"]
    uc = "UC" + "a" * 22
    assert tx.channel_urls(uc) == [f"https://www.youtube.com/channel/{uc}/videos"]


def test_channel_urls_leaves_playlists_and_explicit_tabs_alone():
    assert tx.channel_urls("https://www.youtube.com/playlist?list=PLx") == ["https://www.youtube.com/playlist?list=PLx"]
    assert tx.channel_urls("https://www.youtube.com/@mkbhd/shorts", streams=True) == [
        "https://www.youtube.com/@mkbhd/shorts"]


def test_read_targets_skips_comments_blanks_and_duplicates(tmp_path):
    f = tmp_path / "t.txt"
    f.write_text("﻿# my list\n@mkbhd   # tech\n\ndQw4w9WgXcQ\n@mkbhd\n", encoding="utf-8")
    assert tx.read_targets(f) == ["@mkbhd", "dQw4w9WgXcQ"]


# ── rendering ─────────────────────────────────────────────────────────────────

SNIPS = [{"text": "Hello\nthere.", "start": 0.0, "duration": 2.0},
         {"text": "General  Kenobi!", "start": 3661.5, "duration": 1.25}]
META = {"video_id": "aaaaaaaaaaa", "title": "T", "url": "https://u", "language": "en", "generated": True}


def test_render_srt_and_vtt_timing():
    srt = tx.render("srt", META, SNIPS)
    assert "1\n00:00:00,000 --> 00:00:02,000\nHello there.\n" in srt
    assert "2\n01:01:01,500 --> 01:01:02,750\nGeneral Kenobi!\n" in srt
    vtt = tx.render("vtt", META, SNIPS)
    assert vtt.startswith("WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nHello there.\n")


def test_render_txt_md_json():
    assert tx.render("txt", META, SNIPS) == "Hello there. General Kenobi!\n"
    assert tx.render("txt", META, SNIPS, timestamps=True) == "[00:00] Hello there.\n[1:01:01] General Kenobi!\n"
    md = tx.render("md", META, SNIPS)
    assert md.startswith("# T\n\n- URL: https://u\n- Language: en (auto-generated)\n\n")
    data = json.loads(tx.render("json", META, SNIPS))
    assert data["video_id"] == "aaaaaaaaaaa" and len(data["segments"]) == 2
    with pytest.raises(ValueError):
        tx.render("docx", META, SNIPS)


def test_long_transcripts_are_split_into_paragraphs():
    snips = [{"text": "word " * 30 + "end.", "start": i, "duration": 1} for i in range(20)]
    assert tx.render("txt", META, snips).count("\n\n") >= 3


def test_slug_makes_windows_safe_names():
    assert tx.slug('a<b>:c"/d\\e|f?g*h') == "abcdefgh"
    assert tx.slug("  spaced   out . ") == "spaced out"
    assert tx.slug("???") == "video"
    assert len(tx.slug("x" * 300)) == 80


@pytest.mark.parametrize("name, status", [
    ("IpBlocked", "blocked"), ("RequestBlocked", "blocked"), ("TranscriptsDisabled", "disabled"),
    ("NoTranscriptFound", "no_transcript"), ("VideoUnavailable", "unavailable"),
    ("AgeRestricted", "age_restricted"), ("ValueError", "error")])
def test_classify(name, status):
    assert tx.classify(_error(name)) == status


# ── fetching ──────────────────────────────────────────────────────────────────

def test_fetch_transcript_language_choice_and_any_lang_fallback():
    api = FakeApi({"v": [FakeTranscript("de"), FakeTranscript("fr", generated=True)]})
    snips, lang, gen = tx.fetch_transcript(api, "v", ["fr", "de"], any_lang=False)
    assert (lang, gen, snips[0]["text"]) == ("de", False, "Hello there.")
    with pytest.raises(Exception, match="NoTranscriptFound"):
        tx.fetch_transcript(api, "v", ["en"], any_lang=False)
    assert tx.fetch_transcript(api, "v", ["en"], any_lang=True)[1] == "de"


def test_process_writes_every_format(api, tmp_path):
    opts = tx.Options(out=tmp_path, formats=["txt", "srt"], delay=0)
    r = tx.process(VIDS[0], tmp_path, opts, threading.Event())
    assert r.status == "ok" and r.language == "en" and r.generated == "no"
    assert (tmp_path / "First intro__aaaaaaaaaaa.txt").read_text(encoding="utf-8") == "Hello there. General Kenobi!\n"
    assert (tmp_path / "First intro__aaaaaaaaaaa.srt").exists()


def test_process_recovers_from_a_temporary_block(api, tmp_path):
    api.plan["aaaaaaaaaaa"] = (_error("RequestBlocked"), _error("ConnectionError"), [FakeTranscript()])
    abort = threading.Event()
    r = tx.process(VIDS[0], tmp_path, tx.Options(out=tmp_path, delay=0, retries=3), abort)
    assert r.status == "ok" and not abort.is_set()
    assert len(api.calls) == 3


def test_process_gives_up_and_stops_everyone_after_repeated_blocks(api, tmp_path):
    api.plan["aaaaaaaaaaa"] = _error("RequestBlocked")
    abort = threading.Event()
    r = tx.process(VIDS[0], tmp_path, tx.Options(out=tmp_path, delay=0, retries=3), abort)
    assert r.status == "blocked" and abort.is_set()
    assert len(api.calls) == 4                      # first try + 3 retries


def test_process_does_not_retry_permanent_errors(api, tmp_path):
    api.plan["aaaaaaaaaaa"] = _error("TranscriptsDisabled")
    r = tx.process(VIDS[0], tmp_path, tx.Options(out=tmp_path, delay=0), threading.Event())
    assert r.status == "disabled" and r.detail == "TranscriptsDisabled: details here"
    assert len(api.calls) == 1


# ── whole runs ────────────────────────────────────────────────────────────────

def _index(folder: Path):
    with (folder / "index.csv").open(encoding="utf-8", newline="") as f:
        return {r["video_id"]: r for r in csv.DictReader(f)}


def test_run_target_saves_index_and_resumes(api, monkeypatch, tmp_path):
    fake_videos(monkeypatch, VIDS)
    opts = tx.Options(out=tmp_path, formats=["md"], delay=0, combine=True)
    ok, counts = tx.run_target("@me", opts)
    folder = tmp_path / "My Channel"
    assert ok and counts == {"ok": 2}
    assert {r["status"] for r in _index(folder).values()} == {"ok"}
    combined = (folder / "_all_transcripts.md").read_text(encoding="utf-8")
    assert "# First: intro?" in combined and "# Second" in combined

    api.calls.clear()
    ok, counts = tx.run_target("@me", opts)        # second run: nothing to fetch
    assert ok and counts == {"cached": 2} and api.calls == []
    assert _index(folder)["aaaaaaaaaaa"]["language"] == "en"

    tx.run_target("@me", tx.Options(out=tmp_path, formats=["md"], delay=0, overwrite=True))
    assert len(api.calls) == 2


def test_run_target_fetches_only_missing_formats(api, monkeypatch, tmp_path):
    fake_videos(monkeypatch, VIDS[:1], single=True)
    tx.run_target("x", tx.Options(out=tmp_path, formats=["txt"], delay=0))
    api.calls.clear()
    tx.run_target("x", tx.Options(out=tmp_path, formats=["txt", "srt"], delay=0))
    assert api.calls == ["aaaaaaaaaaa"]             # .srt was missing, so it is fetched again
    assert (tmp_path / "First intro__aaaaaaaaaaa.srt").exists()


def test_run_target_stops_everyone_when_blocked(api, monkeypatch, tmp_path):
    fake_videos(monkeypatch, VIDS)
    api.plan = {v["id"]: _error("IpBlocked") for v in VIDS}
    ok, counts = tx.run_target("@me", tx.Options(out=tmp_path, delay=0, retries=0, workers=1))
    assert not ok
    assert counts.get("blocked", 0) + counts.get("skipped", 0) == 2


# ── the command ───────────────────────────────────────────────────────────────

def test_cli_transcripts(api, monkeypatch, tmp_path):
    fake_videos(monkeypatch, VIDS)
    res = CliRunner().invoke(y.cli, ["transcripts", "@me", "-o", str(tmp_path), "--format", "txt,json"])
    assert res.exit_code == 0, res.output
    assert len(list((tmp_path / "My Channel").glob("*.json"))) == 2
    assert "ok=2" in res.output


def test_cli_transcripts_reads_targets_from_file(api, monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(tx, "enumerate_videos", lambda t, o: (seen.append(t) or [], None, False))
    f = tmp_path / "list.txt"
    f.write_text("@one\n# skip\n@two\n", encoding="utf-8")
    res = CliRunner().invoke(y.cli, ["transcripts", "@zero", "-f", str(f), "-o", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert seen == ["@zero", "@one", "@two"]


def test_cli_transcripts_rejects_bad_input(tmp_path):
    res = CliRunner().invoke(y.cli, ["transcripts"])
    assert res.exit_code == 2 and "at least one" in res.output
    res = CliRunner().invoke(y.cli, ["transcripts", "@me", "--format", "txt,docx"])
    assert res.exit_code == 2 and "docx" in res.output


def test_cli_transcripts_exit_code_when_blocked(api, monkeypatch, tmp_path):
    fake_videos(monkeypatch, VIDS)
    api.plan = {v["id"]: _error("IpBlocked") for v in VIDS}
    res = CliRunner().invoke(y.cli, ["transcripts", "@me", "@other", "-o", str(tmp_path), "--retries", "0"])
    assert res.exit_code == 1
    assert res.output.count("== @") == 1            # stopped before the second target
