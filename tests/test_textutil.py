"""Tests for predictive.textutil.extract_context."""

import pytest

from predictive.textutil import MAX_LOOKBEHIND, extract_context


@pytest.mark.parametrize(
    "text, prefix, context",
    [
        ("", "", []),
        ("prédi", "prédi", []),
        ("La prédi", "prédi", ["La"]),
        ("un deux trois quatre cinq", "cinq", ["deux", "trois", "quatre"]),
        ("hello ", "", ["hello"]),
        ("Bonjour, mon", "mon", ["Bonjour"]),
        ("c'est l'", "l'", ["c'est"]),
        ("c’est l’arb", "l’arb", ["c’est"]),
        ("aujourd'hui", "aujourd'hui", []),
        ("peut-être", "peut-être", []),
        ("peut-", "peut-", []),
        ("fin. Début", "Début", ["fin"]),
        ("snake_case", "case", ["snake"]),
        ("(paren", "paren", []),
        ("x -", "", ["x"]),
    ],
)
def test_extract_context(text, prefix, context):
    assert extract_context(text) == (prefix, context)


def test_max_context():
    assert extract_context("a b c d", max_context=1) == ("d", ["c"])
    assert extract_context("a b c d", max_context=0) == ("d", [])


def test_long_paragraph_is_truncated():
    text = "mot " * 1000 + "fin"
    prefix, context = extract_context(text)
    assert prefix == "fin"
    assert context == ["mot", "mot", "mot"]
    assert len(text) > MAX_LOOKBEHIND
