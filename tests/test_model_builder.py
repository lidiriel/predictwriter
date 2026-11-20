"""Tests for predictive.model_builder (no network: a fake dump server)."""

import bz2
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from predictive import model_builder as mb

ROOT = Path(__file__).resolve().parent.parent
DATE = "20261115"


def dump_file(start, count):
    """A small CirrusSearch dump file (bz2 JSON lines) with ``count`` pages."""
    lines = []
    for i in range(start, start + count):
        lines.append(json.dumps({"index": {"_id": str(i)}}))
        text = "page %d : la nuit tombe sur la ville et le vent se lève" % i
        lines.append(json.dumps({"namespace": 0, "title": "P%d" % i, "text": text}))
    return bz2.compress(("\n".join(lines) + "\n").encode("utf-8"))


class FakeServer:
    """Serves the dump listing and files of frwikisource / frwiki."""

    def __init__(self, files_per_wiki=2, pages=200):
        self.files = {}
        for wiki in ("frwikisource", "frwiki"):
            for part in range(files_per_wiki):
                url = mb.wikipedia_part_url(wiki, DATE, part)
                self.files[url] = dump_file(part * pages, pages)
        self.requests = []

    def fetch_text(self, url):
        self.requests.append(url)
        if url == mb.CIRRUS_BASE:
            return '<a href="20261108/">20261108/</a>\n<a href="%s/">%s/</a>\n' % (
                DATE,
                DATE,
            )
        lines = []
        for file_url, data in self.files.items():
            if file_url.startswith(url):
                name = file_url.rsplit("/", 1)[1]
                lines.append(
                    '<a href="%s">%s</a>       15-Nov-2026 10:15           %d\r'
                    % (name, name, len(data))
                )
        return "\n".join(lines)

    def fetch_range(self, url, start, end):
        data = self.files[url][start : end + 1]
        self.requests.append((url, start, end))
        self.received = getattr(self, "received", 0) + len(data)
        return data


def build(tmp_path, sources, server, **limits):
    events = []
    limits.setdefault("min_count", 1)
    limits.setdefault("pause", 0)
    out = tmp_path / "ngrams.sqlite"
    result = mb.build_model(
        sources,
        str(out),
        "fr_FR",
        mb.BuildLimits(**limits),
        progress=events.append,
        fetch_text=server.fetch_text,
        fetch_range=server.fetch_range,
        sleep=lambda s: None,
    )
    return out, result, events


def test_list_dump_files_parses_the_listing():
    server = FakeServer(files_per_wiki=3)
    files = mb.list_dump_files("frwikisource", DATE, server.fetch_text)
    assert [u.rsplit("-", 1)[1] for u, _ in files] == [
        "00000.json.bz2",
        "00001.json.bz2",
        "00002.json.bz2",
    ]
    assert all(size > 0 for _, size in files)


def test_wikisource_build_reads_files_in_order_and_records_licence(tmp_path):
    server = FakeServer()
    source = mb.WikimediaSource("wikisource", "fr_FR")
    out, result, events = build(tmp_path, [source], server, max_download_bytes=10**9)
    assert (
        result["words"] == 2 * 200 * 12
    )  # 12 words per page (the number and ":" are separators)
    assert source.date == DATE  # latest dump date resolved
    con = sqlite3.connect(str(out))
    meta = dict(con.execute("SELECT key, value FROM _meta"))
    assert "Wikisource (fr)" in meta["licence"] and "CC BY-SA 4.0" in meta["licence"]
    assert meta["frwikisource_dump"] == DATE
    assert "Wikisource (fr), dump %s" % DATE in meta["sources"]
    count = con.execute(
        "SELECT count FROM _2_gram WHERE word_1='la' AND word='nuit'"
    ).fetchone()[0]
    assert count == 400
    kinds = [e["event"] for e in events]
    assert kinds[0] == "start" and kinds[-1] == "done"
    assert "download" in kinds and "source" in kinds
    assert not (tmp_path / "ngrams.sqlite.part").exists()


def test_download_budget_is_shared_and_respected(tmp_path):
    server = FakeServer(files_per_wiki=2)
    first_file = len(next(iter(server.files.values())))
    budget = first_file + 100  # the whole first file and 100 bytes of the next
    sources = [
        mb.WikimediaSource("wikisource", "fr"),
        mb.WikimediaSource("wikipedia", "fr"),
    ]
    out, result, events = build(
        tmp_path, sources, server, max_download_bytes=budget, slice_bytes=500
    )
    ranges = [r for r in server.requests if isinstance(r, tuple)]
    assert server.received == result["downloaded"] == budget  # all of it, no more
    assert not any("frwiki_content" in url for url, _, _ in ranges)  # budget used up
    assert result["words"] > 0


def test_max_words_stops_reading(tmp_path):
    server = FakeServer()
    _, result, _ = build(
        tmp_path,
        [mb.WikimediaSource("wikisource", "fr")],
        server,
        max_words=1000,
        max_download_bytes=10**9,
    )
    assert result["words"] == 1000


def test_folder_and_wikimedia_sources_combined(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "lettre.txt").write_text("Cher ami, je vous écris\n", encoding="utf-8")
    server = FakeServer()
    _, result, events = build(
        tmp_path,
        [mb.FolderSource(str(docs)), mb.WikimediaSource("wikisource", "fr")],
        server,
        max_download_bytes=10**9,
    )
    assert result["sources"] == ["folder", "wikimedia"]
    names = [e["name"] for e in events if e["event"] == "source"]
    assert names[0].startswith("folder ") and names[1].startswith("Wikisource")


def test_cancel_keeps_the_existing_model(tmp_path):
    out = tmp_path / "ngrams.sqlite"
    out.write_bytes(b"previous model")
    server = FakeServer()
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 50  # cancel during the build

    with pytest.raises(mb.Cancelled):
        mb.build_model(
            [mb.WikimediaSource("wikisource", "fr")],
            str(out),
            "fr_FR",
            mb.BuildLimits(min_count=1, pause=0, max_download_bytes=10**9),
            cancel=cancel,
            fetch_text=server.fetch_text,
            fetch_range=server.fetch_range,
            sleep=lambda s: None,
        )
    assert out.read_bytes() == b"previous model"
    assert not (tmp_path / "ngrams.sqlite.part").exists()


def test_missing_folder_is_an_error_and_writes_nothing(tmp_path):
    with pytest.raises(FileNotFoundError):
        build(tmp_path, [mb.FolderSource(str(tmp_path / "nope"))], FakeServer())
    assert not (tmp_path / "ngrams.sqlite").exists()
    assert not (tmp_path / "ngrams.sqlite.part").exists()


def test_estimate():
    limits = mb.BuildLimits(max_download_bytes=250 * mb.MB, max_words=30_000_000)
    est = mb.estimate([mb.WikimediaSource("wikisource", "fr")], limits)
    assert est["download_mb"] == 250
    assert 25_000_000 <= est["words"] <= 30_000_000
    assert est["minutes"] >= 5 and est["model_mb"] > 200
    est = mb.estimate([mb.FolderSource("/tmp")], limits)
    assert est["download_mb"] == 0


def test_unknown_project_is_rejected():
    with pytest.raises(ValueError):
        mb.WikimediaSource("wikibogus", "fr")


def test_json_mode_in_a_separate_process(tmp_path):
    """What the Options page will run: one JSON event per line on stdout."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("un deux trois un deux trois\n" * 50, encoding="utf-8")
    out = tmp_path / "model.sqlite"
    env = dict(os.environ, PYTHONPATH=str(ROOT / "extension" / "python" / "pythonpath"))
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "predictive.model_builder",
            "--json",
            "--out",
            str(out),
            "--source",
            "folder:%s" % docs,
            "--min-count",
            "1",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    events = [json.loads(line) for line in proc.stdout.splitlines()]
    assert events[0]["event"] == "start" and events[-1]["event"] == "done"
    assert events[-1]["words"] == 300 and out.exists()


def test_json_mode_reports_errors(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(ROOT / "extension" / "python" / "pythonpath"))
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "predictive.model_builder",
            "--json",
            "--out",
            str(tmp_path / "m.sqlite"),
            "--source",
            "folder:%s" % (tmp_path / "missing"),
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert proc.returncode == 1
    last = json.loads(proc.stdout.splitlines()[-1])
    assert last["event"] == "error" and "folder not found" in last["message"]
