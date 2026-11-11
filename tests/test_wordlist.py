"""Tests for predictive.wordlist."""

from predictive.wordlist import WordList


def make(*entries):
    return WordList.from_entries(list(entries))


def test_prefix_ranking_by_frequency_then_length():
    wl = make(
        ("prédiction", 50),
        ("prédire", 80),
        ("prédicat", 0),
        ("prédit", 0),
        ("autre", 99),
    )
    assert wl.complete("prédi", 5) == ["prédire", "prédiction", "prédit", "prédicat"]
    assert wl.complete("prédi", 2) == ["prédire", "prédiction"]


def test_exact_word_is_not_suggested():
    wl = make(("pré", 100), ("présent", 1))
    assert wl.complete("pré") == ["présent"]


def test_case_insensitive_and_merged_spellings():
    wl = make(("Paris", 10), ("paris", 3), ("parisien", 1))
    assert wl.complete("par") == ["Paris", "parisien"]
    assert wl.complete("PAR") == ["Paris", "parisien"]
    assert wl.contains("PARIS")


def test_typographic_apostrophe_matches_ascii():
    wl = make(("aujourd'hui", 5))
    assert wl.complete("aujourd’") == ["aujourd'hui"]


def test_no_match_and_empty_prefix():
    wl = make(("chat", 1))
    assert wl.complete("chien") == []
    assert wl.complete("") == []
    assert wl.complete("zzz") == []


def test_load_file(tmp_path):
    path = tmp_path / "x.words.tsv"
    path.write_text("maison\t3\nmaisons\t1\nmaître\t2\n", encoding="utf-8")
    wl = WordList.load(str(path))
    assert len(wl) == 3
    assert wl.complete("mai") == ["maison", "maisons"]  # "î" is not "i"
    assert wl.complete("maî") == ["maître"]
    assert wl.frequency("maître") == 2


def test_candidates_with_elision_bit():
    wl = WordList.from_entries(
        [("arbre", 5, 0, 1), ("arbres", 9, 0, 2), ("ami", 3, 0, 1), ("chat", 99, 0, 0)]
    )

    def words(indices):
        return [wl.word(i) for i in indices]

    assert words(wl.candidates("", 10, elision_bit=1)) == ["arbre", "ami"]
    assert words(wl.candidates("ar", 10, elision_bit=1)) == ["arbre"]
    assert words(wl.candidates("ar", 10)) == ["arbres", "arbre"]
    assert wl.has_grammar


def test_old_two_column_files_still_load(tmp_path):
    path = tmp_path / "x.words.tsv"
    path.write_text("chat\t3\nchien\t5\t1\t0\n", encoding="utf-8")
    wl = WordList.load(str(path))
    assert wl.complete("ch") == ["chien", "chat"]
    assert wl.verb_bits(wl.index_of("chien")) == 1


def test_elision_pairs_rank_first(tmp_path):
    wl = WordList.from_entries(
        [
            ("est", 900, 0, 1),
            ("année", 100, 0, 1),
            ("arbre", 50, 0, 1),
            ("un", 80, 0, 1),
        ]
    )
    pairs = tmp_path / "x.elisions.tsv"
    pairs.write_text("l'\tannée\t70\nl'\tun\t60\nl'\tinconnu\t9\n", encoding="utf-8")
    from predictive.grammar import ELISION_BIT

    bit = ELISION_BIT["l'"]
    assert wl.load_elision_pairs(str(pairs)) == 2  # unknown word ignored
    wl.prepare_elisions()
    order = [wl.word(i) for i in wl.candidates("", 10, bit)]
    assert order == ["année", "un", "est", "arbre"]  # pairs first, then frequency
    assert [wl.word(i) for i in wl.candidates("a", 10, bit)] == ["année", "arbre"]
