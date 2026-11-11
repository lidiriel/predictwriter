"""Tests for the Hunspell affix expander in tools/build_wordlist.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import build_wordlist as bw  # noqa: E402

AFF = """SET UTF-8
FLAG long
NEEDAFFIX ()
FORBIDDENWORD {}
SFX S. Y 2
SFX S. 0 0/L'D' [^sxz]
SFX S. 0 s/L'D' [^sxz]
SFX X. Y 1
SFX X. al aux/L' al
SFX Ve Y 2
SFX Ve er ons/Re er
SFX Ve er ez er
SFX Re Y 1
SFX Re 0 -tu .
PFX L' Y 1
PFX L' 0 l' .
PFX D' Y 1
PFX D' 0 d' .
PFX Un Y 1
PFX Un 0 re .
"""

DIC = """6
arbre/S.
cheval/X.
manger/Ve
lavons/{}
base/()Re
faire/UnVe
"""


def expand_all(tmp_path, keep_elisions=False):
    (tmp_path / "t.aff").write_text(AFF, encoding="utf-8")
    (tmp_path / "t.dic").write_text(DIC, encoding="utf-8")
    aff = bw.read_aff(tmp_path / "t.aff")
    out = set()
    for word, flags, tags in bw.read_dic(tmp_path / "t.dic", aff):
        out |= set(bw.expand(word, flags, aff, keep_elisions, tags))
    return out


def test_expansion(tmp_path):
    forms = expand_all(tmp_path)
    assert {
        "arbre",
        "arbres",
        "cheval",
        "chevaux",
        "manger",
        "mangons",
        "mangez",
    } <= forms
    assert "mangons-tu" in forms  # twofold suffix
    assert "refaire" in forms  # prefix; "faire" does not match the "er" condition
    assert "lavons" not in forms  # forbidden
    assert "base" not in forms and "base-tu" in forms  # needaffix on root
    assert not any("'" in f for f in forms)  # elisions dropped


def test_cross_product_and_elisions_kept(tmp_path):
    forms = expand_all(tmp_path, keep_elisions=True)
    assert {"l'arbre", "d'arbres", "l'chevaux"} <= forms
    assert "refaire" in forms


def test_condition_regex():
    assert bw._condition_regex("[^sxz]", suffix=True).search("arbre")
    assert not bw._condition_regex("[^sxz]", suffix=True).search("bras")
    assert bw._condition_regex("[aeo]u", suffix=True).search("bateau")
    assert bw._condition_regex("i[le]", suffix=False).search("il")


GRAMMAR_AFF = """SET UTF-8
FLAG long
NEEDAFFIX ()
SFX S. Y 2
SFX S. 0 0/L'D' [^sxz] is:sg
SFX S. 0 s/D' [^sxz] is:pl
SFX V. Y 4
SFX V. er er/n'l' er po:infi
SFX V. er e/j'n'l' er po:ipre po:1sg po:3sg
SFX V. er ons/n'l' er po:ipre po:1pl
SFX V. er é/l' er po:ppas
PFX L' Y 1
PFX L' 0 l' .
PFX D' Y 1
PFX D' 0 d' .
PFX j' Y 1
PFX j' 0 j' .
PFX n' Y 1
PFX n' 0 n' .
PFX l' Y 1
PFX l' 0 l' .
"""

GRAMMAR_DIC = """4
arbre/S.() po:nom
chat/S.() po:nom
aimer/V.() po:v1
manger/V.() po:v1
"""


def test_verb_and_elision_bits(tmp_path):
    from predictive import grammar as g

    (tmp_path / "g.aff").write_text(GRAMMAR_AFF, encoding="utf-8")
    (tmp_path / "g.dic").write_text(GRAMMAR_DIC, encoding="utf-8")
    rows = {
        w: (v, e)
        for w, f, v, e in bw.build(
            tmp_path / "g.dic", tmp_path / "g.aff", None, 1, 0, False
        )
    }
    E = g.ELISION_BIT
    assert rows["arbre"] == (0, E["l'"] | E["d'"])
    assert rows["arbres"] == (0, E["d'"])  # no "l'arbres"
    assert rows["chat"] == (0, 0)  # consonant: no elision at all
    assert rows["aime"] == (g.P1SG | g.P3SG, E["j'"] | E["n'"] | E["l'"])
    assert rows["aimons"] == (g.P1PL, E["n'"] | E["l'"])  # no "j'aimons"
    assert rows["aimer"][0] == g.INF
    assert rows["aimé"][0] == g.PPAS
    assert rows["mange"] == (g.P1SG | g.P3SG, 0)  # consonant: no elision
