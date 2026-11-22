"""Translations of the user interface (predictive.i18n)."""

import ast
import re
from pathlib import Path

import pytest

from predictive import build_ui, buildinfo, i18n, keymap

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "extension" / "python" / "pythonpath" / "predictive"
DIALOGS = ROOT / "extension" / "dialogs"


@pytest.fixture
def french():
    i18n.set_language("fr-FR")
    yield
    i18n.set_language("en")


def code_texts():
    """English texts passed to _() in the package, plus the label lists."""
    texts = set()
    for path in PACKAGE.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ("_", "translate")
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                texts.add(node.args[0].value)
    # uno_glue (imports uno) is parsed, not imported: its label lists by name.
    tree = ast.parse((PACKAGE / "uno_glue.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id.endswith("_LABELS") for t in node.targets
        ):
            texts.update(ast.literal_eval(node.value))
    texts.update(keymap.ACCEPT_KEY_LABELS)
    texts.add(build_ui.LICENCE_NOTE)
    return texts


def dialog_texts():
    """Labels and titles of the .xdl dialogs (numbers left out)."""
    texts = set()
    for path in DIALOGS.glob("*.xdl"):
        xml = path.read_text(encoding="utf-8")
        for value in re.findall(r'dlg:(?:value|title)="([^"]+)"', xml):
            value = value.replace("&amp;", "&").replace("&quot;", '"')
            if not re.fullmatch(r"[\d.]+", value):
                texts.add(value)
    return texts


def test_language_codes():
    assert i18n.normalize("fr-FR") == "fr"
    assert i18n.normalize("fr_CA.UTF-8") == "fr"
    assert i18n.normalize("en-US") == "en"
    assert i18n.normalize("") == "en"
    assert i18n.set_language("de") == "de" and i18n._("Close") == "Close"
    i18n.set_language("en")


def test_english_by_default():
    assert i18n.language() == "en"
    assert i18n._("Close") == "Close"
    assert i18n._("not in any catalog") == "not in any catalog"
    assert i18n.number("12.3") == "12.3"


def test_french(french):
    assert i18n._("Close") == "Fermer"
    assert i18n._("not in any catalog") == "not in any catalog"
    assert i18n._("Fermer") == "Fermer"  # translating twice changes nothing
    assert i18n.number("12.3") == "12,3"


def test_every_text_has_a_french_translation():
    missing = sorted((code_texts() | dialog_texts()) - set(i18n.FR))
    assert not missing, "add to i18n.FR: %r" % missing


def test_no_unused_french_text():
    unused = sorted(set(i18n.FR) - code_texts() - dialog_texts())
    assert not unused, "unused in i18n.FR: %r" % unused


def test_placeholders_match():
    for english, french in i18n.FR.items():
        assert re.findall(r"%[.\d]*[sdf]", english) == re.findall(
            r"%[.\d]*[sdf]", french
        ), english


def test_build_dialog_texts_in_french(french, tmp_path):
    text = build_ui.estimate_text(["wikisource"], "fr_FR", 10, 30, 2)
    assert text.startswith("Environ 1,7 M mots, 10 Mo à télécharger")
    assert "remplacera" in text
    assert build_ui.progress({"event": "error", "message": "x"}, 1)[0] == ("Erreur : x")
    reading = build_ui.progress({"event": "source", "name": "folder /docs"}, 1)[0]
    assert reading == "Lecture de dossier /docs…"
    dump = build_ui.source_name("Wikisource (fr), dump latest")
    assert dump == "Wikisource (fr), copie la plus récente"
    assert build_ui.source_name("Wikisource (fr), dump 20261115") == (
        "Wikisource (fr), copie du 20261115"
    )
    pdf = build_ui.progress({"event": "skipped", "files": 3, "reason": "x"}, 1)[0]
    assert pdf.startswith("3 fichier(s) PDF ignorés")


def test_build_dialog_texts_in_english():
    assert build_ui.source_name("folder /docs") == "folder /docs"
    assert build_ui.source_name("Wikisource (fr), dump 20261115") == (
        "Wikisource (fr), dump 20261115"
    )
    pdf = build_ui.progress({"event": "skipped", "files": 3, "reason": "x"}, 1)[0]
    assert pdf == "3 PDF file(s) skipped: install poppler-utils to read PDF files"


def test_version_line(french):
    info = {
        "version": "1.4",
        "commit": "abc1234",
        "commit_date": "2026-11-22T10:00:00+01:00",
        "commits_since_tag": 2,
        "modified": True,
    }
    assert buildinfo.describe(info) == (
        "Predictive Writer 1.4 (+2 commits) — commit abc1234 du 2026-11-22 10:00"
        " (avec des modifications non validées)"
    )
    assert buildinfo.describe(info, translated=False) == (
        "Predictive Writer 1.4 (+2 commits) — commit abc1234 of 2026-11-22 10:00"
        " (with uncommitted changes)"
    )
