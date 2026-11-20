"""Tests for predictive.build_ui (texts of the "Build model" dialog)."""

from predictive import build_ui as ui
from predictive import model_builder as mb


def test_sources_folder_first_and_only_when_chosen():
    assert ui.sources(True, " /docs ", True, "wikipedia") == [
        "folder:/docs",
        "wikipedia",
    ]
    assert ui.sources(True, "", True, "wikisource") == ["wikisource"]
    assert ui.sources(False, "/docs", False, "wikisource") == []
    assert ui.sources(False, "", True, "bogus") == []


def test_projects_match_the_builder():
    assert set(ui.PROJECTS) == set(mb.WIKIMEDIA_PROJECTS)
    assert ui.PROJECT_LABELS[0] == "Wikisource"


def test_estimate_text(tmp_path):
    assert "Choose" in ui.estimate_text([], "fr_FR", 250, 30, 2)
    missing = "folder:%s" % (tmp_path / "missing")
    assert "not found" in ui.estimate_text([missing], "fr_FR", 250, 30, 2)
    text = ui.estimate_text(["wikisource"], "fr_FR", 10, 30, 2)
    assert "1.7 M words" in text and "10 MB to download" in text
    assert "replaces" in text
    (tmp_path / "a.txt").write_text("x" * 60_000, encoding="utf-8")
    text = ui.estimate_text(["folder:%s" % tmp_path], "fr_FR", 10, 30, 2)
    assert "10 000 words" in text and "download" not in text


def test_progress_texts():
    text, value = ui.progress(
        {"event": "words", "words": 3_000_000, "words_per_second": 500_000}, 30_000_000
    )
    assert text.startswith("3.0 M words read") and value == 9
    text, value = ui.progress(
        {
            "event": "download",
            "source": "Wikisource (fr)",
            "downloaded": 50 * mb.MB,
            "budget": 100 * mb.MB,
        },
        1,
    )
    assert "50 of 100 MB" in text and value == 47
    text, value = ui.progress({"event": "installed", "words": 1234, "seconds": 2}, 1)
    assert "now in use" in text and value == 100
    assert ui.progress({"event": "error", "message": "boom"}, 1)[0] == "Error: boom"
    assert ui.progress({"event": "output", "text": "x"}, 1) == ("", None)
    assert ui.finished({"event": "cancelled"}) and not ui.finished({"event": "words"})
    assert not ui.finished(None)
