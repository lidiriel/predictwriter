"""Regression tests on the real French data (skipped if not generated).

They pin down the behaviour asked for: frequency order, verb agreement and
suggestions right after an elided word.
"""

import os

import pytest

from predictive.engine import DEFAULT_DATA_DIR, Engine

WORDS = os.path.join(DEFAULT_DATA_DIR, "fr_FR.words.tsv")
pytestmark = pytest.mark.skipif(not os.path.exists(WORDS), reason="no word list")


@pytest.fixture(scope="module")
def engine():
    eng = Engine(max_suggestions=6)
    eng.preload("fr_FR", background=False)
    return eng


@pytest.mark.parametrize(
    "context, prefix, expected_first",
    [
        # 3. frequency order
        ([], "com", "comme"),
        ([], "pou", "pour"),  # 1 letter added: allowed (min_completion 1)
        # 2. verb agreement
        (["je"], "mang", "mange"),
        (["tu"], "mang", "manges"),
        (["nous"], "mang", "mangeons"),
        (["vous"], "mang", "mangez"),
        (["ils"], "mang", "mangent"),
        (["j'ai"], "mang", "mangé"),
        (["elle", "est"], "arriv", "arrivé"),
        (["il", "faut"], "mang", "manger"),
        (["je", "vais"], "fai", "faire"),
        (["nous"], "som", "sommes"),
        (["vous"], "fai", "faites"),
        # 1. right after an elided word
        ([], "j'", "j'ai"),
        ([], "qu'", "qu'il"),
        ([], "n'", "n'est"),
        ([], "c'", "c'est"),
        ([], "l'arb", "l'arbre"),
        ([], "J’a", "J’ai"),
    ],
)
def test_first_suggestion(engine, context, prefix, expected_first):
    assert engine.suggest(prefix, context, lang="fr_FR")[0] == expected_first


@pytest.mark.parametrize("prefix", ["con", "com"])
def test_rare_proper_nouns_not_first(engine, prefix):
    assert engine.suggest(prefix, lang="fr_FR")[0] not in ("conan", "condé")


def test_elision_never_offers_forbidden_forms(engine):
    assert "l'arbres" not in engine.suggest("l'arb", lang="fr_FR")
    assert all(not w.startswith("l'c") for w in engine.suggest("l'", lang="fr_FR"))


def test_object_pronoun_then_verb(engine):
    out = engine.suggest("l'", ["je"], lang="fr_FR")
    assert out[0] in ("l'ai", "l'aime", "l'avais")
