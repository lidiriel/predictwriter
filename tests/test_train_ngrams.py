"""Tests for tools/train_ngrams.py (no network, no LibreOffice)."""

import bz2
import io
import json
import sqlite3
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import pressagio.tokenizer  # noqa: E402
import train_ngrams as tn  # noqa: E402

from predictive.nextword import normalize  # noqa: E402

SAMPLES = [
    "Aujourd'hui, le chat dort sur l'arbre.",
    "Peut-être qu’il viendra demain ; qui sait ?",
    "En 1998, la France a gagné 3 à 0 !",
    "« Bonjour » dit-il — puis il partit…",
    "Le 'mot' et -tiret- en bord de mot",
    "UNE PHRASE EN MAJUSCULES",
    "un  espace insécable et\ttab",
]


def pressagio_tokens(text):
    """Reference: what pressagio's own tokenizer produces."""
    tok = pressagio.tokenizer.ForwardTokenizer(
        pressagio.tokenizer.preprocess(normalize(text)) + " "
    )
    tok.lowercase = True
    out = []
    while tok.has_more_tokens():
        t = tok.next_token()
        if t:
            out.append(t)
    return out


@pytest.mark.parametrize("text", SAMPLES)
def test_tokenize_matches_pressagio(text):
    assert tn.tokenize(text) == pressagio_tokens(text)


def test_tokenize_examples():
    assert tn.tokenize("Aujourd’hui, l’arbre") == ["aujourd'hui", "l'arbre"]
    assert tn.tokenize("Peut-être 42 fois") == ["peut-être", "fois"]


def _bz2_lines(docs, streams=1):
    lines = []
    for doc in docs:
        lines.append(json.dumps({"index": {"_id": "1"}}))
        lines.append(json.dumps(doc))
    data = ("\n".join(lines) + "\n").encode()
    if streams == 1:
        return bz2.compress(data)
    half = len(data) // 2
    return bz2.compress(data[:half]) + bz2.compress(data[half:])


@pytest.mark.parametrize("streams", [1, 2])
def test_iter_cirrus_texts(streams):
    docs = [
        {"title": "A", "namespace": 0, "text": "premier article"},
        {"title": "Talk", "namespace": 1, "text": "discussion"},
        {"title": "B", "namespace": 0, "text": "second article " * 50},
        {"title": "C", "namespace": 0},  # no text
    ]
    data = _bz2_lines(docs, streams)
    texts = list(tn.iter_cirrus_texts(io.BytesIO(data), chunk_size=97))
    assert texts == ["premier article", "second article " * 50]


def test_wikipedia_part_url():
    url = tn.wikipedia_part_url("frwiki", "20261108", 3)
    assert url == (
        "https://dumps.wikimedia.org/other/cirrus_search_index/20261108/"
        "index_name=frwiki_content/frwiki_content-20261108-00003.json.bz2"
    )


def test_odt_text(tmp_path):
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
        ' xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:body><office:text>'
        "<text:h>Titre</text:h>"
        '<text:p>Un<text:s/>texte<text:s text:c="2"/>avec <text:span>style</text:span>'
        "<text:note><text:note-body><text:p>note</text:p></text:note-body></text:note>.</text:p>"
        "</office:text></office:body></office:document-content>"
    )
    path = tmp_path / "doc.odt"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("content.xml", content)
    paragraphs = list(tn.iter_odt_text(path))
    assert paragraphs[0] == "Titre"
    assert paragraphs[1] == "Un texte  avec style."


def test_train_counts_prunes_and_records_meta(tmp_path):
    texts = [("t", "le chat dort. le chat mange. le chien dort")] * 2
    out = str(tmp_path / "m.sqlite")
    result = tn.train(texts, out, order=3, min_count=2, meta={"licence": "x"})
    assert result["words"] == 18  # 9 words, twice
    con = sqlite3.connect(out)
    assert con.execute("SELECT count FROM _1_gram WHERE word='le'").fetchone()[0] == 6
    assert (
        con.execute(
            "SELECT count FROM _2_gram WHERE word_1='le' AND word='chat'"
        ).fetchone()[0]
        == 4
    )
    assert (
        con.execute(
            "SELECT count FROM _3_gram WHERE word_2='le' AND word_1='chat' AND word='dort'"
        ).fetchone()[0]
        == 2
    )
    meta = dict(con.execute("SELECT key, value FROM _meta"))
    assert meta["licence"] == "x" and meta["words"] == "18" and meta["order"] == "3"
    assert not (tmp_path / "m.sqlite.part").exists()


def test_max_words_and_batches(tmp_path):
    texts = (("t", "un deux trois quatre") for _ in range(100))
    out = str(tmp_path / "m.sqlite")
    result = tn.train(texts, out, min_count=1, max_words=10)
    assert result["words"] == 10
    con = sqlite3.connect(out)
    assert con.execute("SELECT SUM(count) FROM _1_gram").fetchone()[0] == 10


def test_range_reader_slices_and_limit():
    import random

    rng = random.Random(0)  # random words compress poorly: many slices needed
    articles = [
        "mot%d " % i + " ".join("%08x" % rng.getrandbits(32) for _ in range(5))
        for i in range(2000)
    ]
    data = _bz2_lines([{"namespace": 0, "text": a} for a in articles])
    assert len(data) > 10_000
    calls = []

    def fetch(url, start, end):
        calls.append((start, end))
        return data[start : end + 1]

    # Whole file, in small slices: every article comes through, in order.
    reader = tn.RangeReader(
        "u", slice_size=1000, max_slices=10_000, fetch=fetch, sleep=lambda s: None
    )
    texts = list(tn.iter_cirrus_texts(reader, chunk_size=300))
    assert texts == articles
    assert calls[0] == (0, 999) and calls[1] == (1000, 1999)
    assert reader.downloaded == len(data)

    # Capped: stops after max_slices requests, with complete articles only.
    calls.clear()
    pauses = []
    reader = tn.RangeReader(
        "u", slice_size=1000, max_slices=3, fetch=fetch, sleep=pauses.append
    )
    texts = list(tn.iter_cirrus_texts(reader, chunk_size=300))
    assert len(calls) == 3 and reader.downloaded == 3000 and pauses == [2.0, 2.0]
    assert texts == articles[: len(texts)]  # complete articles, in order
