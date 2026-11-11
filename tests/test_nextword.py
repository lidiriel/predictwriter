"""Tests for predictive.nextword (pressagio wrapper), on a small trained model."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import train_ngrams as tn  # noqa: E402

from predictive.nextword import NextWordPredictor, model_info, normalize  # noqa: E402

CORPUS = [
    "je voudrais savoir si vous venez demain",
    "je voudrais savoir pourquoi il pleut",
    "je voudrais vous dire merci",
    "il fait beau aujourd'hui",
    "aujourd'hui il fait froid",
    "aujourd'hui je travaille",
    "la prédiction du temps est difficile",
    "une prédiction juste",
    "le prédicat de la phrase",
    "100% des gens disent 50_50",
]


@pytest.fixture(scope="module")
def model(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("model") / "ngrams.sqlite")
    texts = [("t", line) for line in CORPUS * 3]
    tn.train(texts, path, order=3, min_count=1, meta={"licence": "test"})
    return path


@pytest.fixture
def predictor(model):
    p = NextWordPredictor(model, max_results=10)
    yield p
    p.close()


def test_next_word_uses_context(predictor):
    assert predictor.predict(["je", "voudrais"])[0] == "savoir"
    assert predictor.predict(["voudrais", "savoir"])[:2] in (
        ["si", "pourquoi"],
        ["pourquoi", "si"],
    )


def test_completion_ranked_by_context(predictor):
    # Without context, "prédicat" and "prédiction" compete; "une" favours prédiction.
    assert predictor.predict(["une"], "pré")[0] == "prédiction"
    assert predictor.predict(["le"], "pré")[0] == "prédicat"


def test_apostrophe_prefix_does_not_break_sql(predictor):
    assert predictor.predict([], "aujourd'h") == ["aujourd'hui"]
    assert predictor.predict([], "aujourd’h") == ["aujourd'hui"]  # typographic
    assert predictor.predict(["il", "fait"], "b") == ["beau"]


def test_like_wildcards_are_literal(predictor):
    assert predictor.predict([], "%") == []
    assert predictor.predict([], "_") == []
    assert predictor.predict([], "pr_") == []


def test_prefix_itself_not_returned(predictor):
    assert "savoir" not in predictor.predict(["voudrais"], "savoir")


def test_case_insensitive_context_and_prefix(predictor):
    assert predictor.predict(["Je", "Voudrais"])[0] == "savoir"
    assert predictor.predict([], "AUJOURD")[0] == "aujourd'hui"


def test_no_context(predictor):
    words = predictor.predict([])
    assert words and len(words) <= 10


def test_model_opened_read_only_and_missing_file(model, tmp_path):
    missing = str(tmp_path / "missing.sqlite")
    with pytest.raises(FileNotFoundError):
        NextWordPredictor(missing)
    assert not os.path.exists(missing)  # pressagio would have created it
    assert model_info(model)["licence"] == "test"
    assert model_info(missing) == {}


def test_normalize():
    assert normalize("l’arbre ; x") == "l'arbre ; x"
