"""Tests for predictive.engine."""

import time

import pytest

from predictive.engine import Engine
from predictive.wordlist import WordList


@pytest.fixture
def engine():
    eng = Engine(min_prefix=3, max_suggestions=3)
    eng.set_wordlist(
        "fr_FR",
        WordList.from_entries(
            [
                ("prédiction", 9),
                ("prédire", 5),
                ("arbre", 3),
                ("arbres", 1),
                ("aujourd'hui", 4),
                ("peut-être", 2),
                ("Allemagne", 1),
                ("pré", 9),
                ("ANTAI", 9),
                ("antenne", 1),
            ]
        ),
    )
    eng.set_wordlist("en_US", WordList.from_entries([("hello", 1)]))
    return eng


def test_basic_and_capitalisation(engine):
    assert engine.suggest("prédi", lang="fr_FR") == ["prédiction", "prédire"]
    assert engine.suggest("Prédi", lang="fr_FR") == ["Prédiction", "Prédire"]
    assert engine.suggest("PRÉDI", lang="fr_FR") == ["PRÉDICTION", "PRÉDIRE"]
    assert engine.suggest("allem", lang="fr_FR") == ["allemagne"]


def test_acronym_needs_matching_case(engine):
    assert engine.suggest("ant", lang="fr_FR") == ["antenne"]
    assert engine.suggest("ANT", lang="fr_FR") == ["ANTAI", "ANTENNE"]


def test_one_letter_completions_are_skipped():
    eng = Engine(min_completion=2)  # the setting; the default is now 1
    eng.set_wordlist("fr_FR", WordList.from_entries([("chats", 9), ("chateau", 1)]))
    assert eng.suggest("chat", lang="fr_FR") == ["chateau"]


def test_min_prefix(engine):
    assert engine.suggest("pr", lang="fr_FR") == []


def test_elision(engine):
    assert engine.suggest("l'arb", lang="fr_FR") == ["l'arbre", "l'arbres"]
    assert engine.suggest("L’arb", lang="fr_FR")[0] == "L’arbre"
    assert engine.suggest("qu'ar", lang="fr_FR") == []  # stem "ar" too short
    assert engine.suggest("l'arb", lang="en_US") == []  # no elision in English


def test_apostrophe_style(engine):
    assert engine.suggest("aujou", lang="fr_FR") == ["aujourd'hui"]
    assert engine.suggest("aujou", lang="fr_FR", apostrophe="’") == ["aujourd’hui"]
    assert engine.suggest("aujourd’h", lang="fr_FR") == ["aujourd’hui"]


def test_hyphen(engine):
    assert engine.suggest("peut-", lang="fr_FR") == ["peut-être"]


def test_unknown_language_loads_lazily(tmp_path):
    (tmp_path / "xx_XX.words.tsv").write_text("bonjour\t1\n", encoding="utf-8")
    eng = Engine(data_dir=str(tmp_path))
    assert eng.suggest("bon", lang="xx_XX") == []  # not loaded yet
    deadline = time.monotonic() + 5  # the loader thread may already be done
    while not eng.is_loaded("xx_XX") and time.monotonic() < deadline:
        time.sleep(0.01)
    assert eng.suggest("bon", lang="xx_XX") == ["bonjour"]


def test_missing_language_file(tmp_path):
    eng = Engine(data_dir=str(tmp_path))
    eng.preload("zz_ZZ", background=False)
    assert eng.suggest("abc", lang="zz_ZZ") == []


@pytest.fixture
def merger():
    eng = Engine(min_prefix=3, max_suggestions=4, min_completion=2)
    eng.set_wordlist(
        "fr_FR",
        WordList.from_entries(
            [
                ("prédiction", 1),
                ("prédicat", 1),
                ("Paris", 1),
                ("arbre", 1),
                ("aujourd'hui", 1),
                ("savoir", 1),
                ("je", 1),
                ("tu", 1),
                ("il", 1),
                ("on", 1),
                ("le", 1),
                ("la", 1),
            ]
        ),
    )
    return eng


def test_merge_model_words_first_then_dictionary(merger):
    out = merger.merge_context(
        "pré", ["prédiction", "prédxyzunknown"], ["prédicat", "prédiction"], "fr_FR"
    )
    assert out == ["prédiction", "prédicat"]  # unknown model word dropped, no dup


def test_merge_keeps_typed_case_and_dictionary_spelling(merger):
    assert merger.merge_context("Pré", ["prédiction"], [], "fr_FR") == ["Prédiction"]
    assert merger.merge_context("PRÉ", ["prédiction"], [], "fr_FR") == ["PRÉDICTION"]
    assert merger.merge_context("par", ["paris"], [], "fr_FR") == ["paris"]
    assert merger.merge_context("", ["paris"], [], "fr_FR") == ["Paris"]


def test_merge_next_word_short_words_and_sentence_start(merger):
    out = merger.merge_context("", ["je", "savoir", "zzzz", "und"], [], "fr_FR")
    assert out == ["je", "savoir"]  # words not in the dictionary are dropped
    out = merger.merge_context("", ["je", "savoir"], [], "fr_FR", sentence_start=True)
    assert out == ["Je", "Savoir"]


def test_merge_elision_and_apostrophe_style(merger):
    out = merger.merge_context("l’ar", ["l'arbre"], [], "fr_FR")
    assert out == ["l’arbre"]
    out = merger.merge_context("", ["aujourd'hui"], [], "fr_FR", apostrophe="’")
    assert out == ["aujourd’hui"]


def test_merge_min_completion_and_limit(merger):
    assert merger.merge_context("savoi", ["savoir"], [], "fr_FR") == []  # adds 1 letter
    many = ["je", "tu", "il", "on", "le", "la"]
    assert len(merger.merge_context("", many, [], "fr_FR")) == 4


@pytest.fixture
def french():
    """Small word list with grammar bits, like build_wordlist.py produces."""
    from predictive import grammar as g

    E = g.ELISION_BIT
    eng = Engine(min_prefix=3, max_suggestions=5, min_completion=2)
    eng.set_wordlist(
        "fr_FR",
        WordList.from_entries(
            [
                # word, frequency, verb bits, elision bits
                ("mange", 900, g.P1SG | g.P3SG, 0),
                ("manger", 800, g.INF, 0),
                ("mangé", 700, g.PPAS, 0),
                ("mangent", 600, g.P3PL, 0),
                ("mangez", 500, g.P2PL, 0),
                ("mangeons", 100, g.P1PL, 0),
                ("mangeoire", 50, 0, 0),
                ("ai", 1000, g.P1SG, E["j'"] | E["n'"]),
                ("aime", 900, g.P1SG | g.P3SG, E["j'"] | E["n'"] | E["l'"]),
                ("avais", 800, g.P1SG | g.P2SG, E["j'"] | E["n'"]),
                ("arbre", 700, 0, E["l'"] | E["d'"]),
                ("arbres", 690, 0, E["d'"]),
                ("année", 680, 0, E["l'"] | E["d'"]),
                ("aimons", 300, g.P1PL, E["n'"] | E["l'"]),
                ("chat", 950, 0, 0),
            ]
        ),
    )
    return eng


def test_frequency_order_without_context(french):
    assert french.suggest("man", lang="fr_FR")[:3] == ["mange", "manger", "mangé"]


@pytest.mark.parametrize(
    "context, first",
    [
        (["nous"], "mangeons"),
        (["vous"], "mangez"),
        (["ils"], "mangent"),
        (["je"], "mange"),
        (["j'ai"], "mangé"),
        (["il", "faut"], "manger"),
        (["je", "vais"], "manger"),
    ],
)
def test_conjugation_moves_agreeing_form_first(french, context, first):
    assert french.suggest("man", context, lang="fr_FR")[0] == first


def test_conflicting_forms_go_last(french):
    out = french.suggest("man", ["nous"], lang="fr_FR")
    assert out[0] == "mangeons"
    assert out.index("manger") < out.index("mange")  # neutral before conflicting


def test_elision_suggests_at_once_and_filters(french):
    assert french.suggest("j'", lang="fr_FR")[:3] == ["j'ai", "j'aime", "j'avais"]
    # Capital and typographic apostrophe kept; "J’ai" would add only 1 letter.
    assert french.suggest("J\u2019a", lang="fr_FR")[:2] == [
        "J\u2019aime",
        "J\u2019avais",
    ]
    out = french.suggest("l'", lang="fr_FR")
    assert out[:3] == ["l'aime", "l'arbre", "l'année"]
    assert "l'arbres" not in out and "l'chat" not in out
    assert french.suggest("d'arb", lang="fr_FR") == ["d'arbre", "d'arbres"]


def test_completes(french):
    assert french.completes("man", "fr_FR")
    assert not french.completes("ma", "fr_FR")
    assert french.completes("j'", "fr_FR") and french.completes("l'a", "fr_FR")


def test_merge_applies_agreement_and_elision(french):
    out = french.merge_context(
        "man", ["mange", "mangeons"], [], "fr_FR", context=["nous"]
    )
    assert out[0] == "mangeons"
    out = french.merge_context("l'", ["l'arbres", "l'arbre"], [], "fr_FR")
    assert out == ["l'arbre"]


def test_default_min_completion_offers_one_letter_forms(french):
    eng = Engine()
    assert eng.min_completion == 1
    french.min_completion = 1
    assert french.suggest("mang", ["je"], lang="fr_FR")[0] == "mange"
    assert french.suggest("mang", ["j'ai"], lang="fr_FR")[0] == "mangé"
