"""Tests for predictive.grammar."""

import pytest

from predictive import grammar as g


@pytest.mark.parametrize(
    "context, bits",
    [
        (["je"], g.P1SG),
        (["j'"], g.P1SG),
        (["tu"], g.P2SG),
        (["elle"], g.P3SG),
        (["on"], g.P3SG),
        (["nous"], g.P1PL),
        (["vous"], g.P2PL),
        (["ils"], g.P3PL),
        (["je", "ne", "le"], g.P1SG),  # object pronoun and negation skipped
        (["je", "vous"], g.P1SG),  # "vous" is the object here
        (["nous", "nous"], g.P1PL),
        (["qu'il"], g.P3SG),  # elided word split
        (["s’ils"], g.P3PL),  # typographic apostrophe
        (["j'ai"], g.PPAS),
        (["je", "n'ai", "pas"], g.PPAS),
        (["elle", "est"], g.PPAS),
        (["il", "a", "bien"], g.PPAS),
        (["il", "faut"], g.INF),
        (["je", "vais"], g.INF),
        (["pour"], g.INF),
        (["le", "chat"], 0),  # noun subjects: not handled
        ([], 0),
    ],
)
def test_expectation(context, bits):
    assert g.expectation(context).bits == bits


def test_agreement_groups():
    nous = g.expectation(["nous"])
    assert g.agreement_group(g.P1PL, nous) == 0  # mangeons
    assert g.agreement_group(g.P1SG | g.P3SG, nous) == 2  # mange
    assert g.agreement_group(g.INF, nous) == 1  # manger: neutral
    assert g.agreement_group(0, nous) == 1  # a noun
    ppas = g.expectation(["j'ai"])
    assert g.agreement_group(g.PPAS, ppas) == 0  # mangé
    assert g.agreement_group(g.P1SG, ppas) == 2  # mange
    assert g.agreement_group(g.INF, ppas) == 1
    assert g.agreement_group(g.P1SG, g.NONE) == 1


def test_helpers():
    assert g.verb_bits(["po:ipre", "po:1sg", "po:3sg"]) == g.P1SG | g.P3SG
    assert g.can_elide("arbre") and g.can_elide("Hôtel") and not g.can_elide("chat")
    assert g.elision_key("L’") == "l'"
    assert g.split_elisions(["J'ai", "mangé"]) == ["j'", "ai", "mangé"]
