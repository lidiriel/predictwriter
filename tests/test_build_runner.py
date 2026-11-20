"""Tests for predictive.build_runner (real child processes, no network)."""

import os
import sys
import threading

from predictive import build_runner as br


def run(tmp_path, sources, cancel_on=None, **kwargs):
    out = str(tmp_path / "model.sqlite.new")
    command = br.builder_command(
        sys.executable,
        out,
        "fr_FR",
        sources,
        max_download_mb=kwargs.get("max_download_mb", 1),
        max_words=kwargs.get("max_words", 30_000_000),
        min_count=1,
    )
    events = []
    finished = threading.Event()
    result = {}
    process = None

    def on_event(event):
        events.append(event)
        if cancel_on and event.get("event") == cancel_on:
            process.cancel()

    def on_exit(code, last):
        result.update(code=code, last=last)
        finished.set()

    process = br.BuildProcess(command, out, on_event, on_exit)
    process.start()
    assert finished.wait(120), "build did not finish"
    return out, events, result, process


def test_build_in_a_child_process(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("le chat dort sur le lit\n" * 100, encoding="utf-8")
    out, events, result, process = run(tmp_path, ["folder:%s" % docs])
    assert result["code"] == 0 and result["last"]["event"] == "done"
    assert result["last"]["words"] == 600 and os.path.exists(out)
    assert events[0]["event"] == "start" and not process.running


def test_error_is_reported(tmp_path):
    out, events, result, _ = run(tmp_path, ["folder:%s" % (tmp_path / "missing")])
    assert result["code"] == 1
    assert result["last"]["event"] == "error"
    assert "folder not found" in result["last"]["message"]
    assert not os.path.exists(out)


def test_cancel_stops_the_child_and_cleans_up(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    line = " ".join("mot%d" % (i % 997) for i in range(1000)) + "\n"
    (docs / "big.txt").write_text(line * 3000, encoding="utf-8")  # 3 M words
    out, events, result, process = run(
        tmp_path, ["folder:%s" % docs], cancel_on="words"
    )
    assert result["code"] != 0
    assert result["last"]["event"] == "cancelled" and process.cancelled
    assert not os.path.exists(out) and not os.path.exists(out + ".part")


def test_find_python_checks_candidates():
    calls = []

    def check(path):
        calls.append(path)
        return path == "/b/python3"

    assert (
        br.find_python(candidates=["/a/python3", "/b/python3", "/c"], check=check)
        == "/b/python3"
    )
    assert calls == ["/a/python3", "/b/python3"]
    assert br.find_python(candidates=["/x"], check=lambda p: False) is None


def test_current_interpreter_works_and_is_a_candidate():
    assert br.works(sys.executable)
    assert sys.executable in br.python_candidates()
    assert not br.works(os.devnull)


def test_candidates_include_libreoffice_program_dir(tmp_path):
    names = br.python_candidates(program_dir=str(tmp_path))
    expected = "python.exe" if os.name == "nt" else "python3"
    assert os.path.normpath(os.path.join(str(tmp_path), expected)) in names
