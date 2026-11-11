"""French grammar hints for ranking (plain Python, no ``uno``).

Two kinds of per-word information come from the Hunspell dictionary
(``tools/build_wordlist.py`` stores them as integer bit masks):

* **verb bits**: grammatical person/number of a conjugated form, infinitive,
  past participle, present participle (Hunspell tags ``po:1sg``...);
* **elision bits**: which elided words may precede the word (``l'``, ``j'``,
  ``qu'``...), from Hunspell's prefix flags, restricted to words starting
  with a vowel or an h (the flags alone are broader).

``expectation(context)`` looks at the words before the cursor and says what
kind of verb form is likely next: a finite form agreeing with a subject
pronoun (``nous`` -> 1st plural), a past participle after avoir/être, or an
infinitive after a modal verb or a preposition. Words matching the
expectation are moved up; nothing is removed.
"""

import re
from typing import Iterable, List, NamedTuple, Optional, Sequence

# Verb bits ---------------------------------------------------------------------
P1SG, P2SG, P3SG, P1PL, P2PL, P3PL = (1 << i for i in range(6))
PERSONS = P1SG | P2SG | P3SG | P1PL | P2PL | P3PL
INF = 1 << 6  # infinitive
PPAS = 1 << 7  # past participle
PPRE = 1 << 8  # present participle

HUNSPELL_VERB_TAGS = {
    "po:1sg": P1SG,
    "po:1isg": P1SG,  # "puissé-je"
    "po:1jsg": P1SG,
    "po:2sg": P2SG,
    "po:3sg": P3SG,
    "po:1pl": P1PL,
    "po:2pl": P2PL,
    "po:3pl": P3PL,
    "po:infi": INF,
    "po:ppas": PPAS,
    "po:ppre": PPRE,
}

# Elision bits ------------------------------------------------------------------
ELISIONS = (
    "l'",
    "d'",
    "qu'",
    "j'",
    "m'",
    "t'",
    "s'",
    "n'",
    "c'",
    "jusqu'",
    "lorsqu'",
    "puisqu'",
    "quoiqu'",
)
ELISION_BIT = {e: 1 << i for i, e in enumerate(ELISIONS)}
VOWELS_AND_H = set("aàâäeéèêëiîïoôöuùûüyÿhœæ")


def verb_bits(tags: Iterable[str]) -> int:
    """Verb bits for the Hunspell morphological tags of one affix rule.

    Imperative persons are ignored: an imperative has no subject pronoun, so
    "tu mange" must not count "mange" (imperative 2sg) as agreeing.
    """
    tags = list(tags)
    imperative = "po:impe" in tags
    bits = 0
    for tag in tags:
        bit = HUNSPELL_VERB_TAGS.get(tag, 0)
        if imperative and bit & PERSONS:
            continue
        bits |= bit
    return bits


def can_elide(word: str) -> bool:
    """True if an elided word (l', j'...) may precede ``word`` phonetically."""
    return bool(word) and word[0].lower() in VOWELS_AND_H


def elision_key(head: str) -> str:
    """Normalised elision ("L’" -> "l'") as used in ``ELISION_BIT``."""
    return head.lower().replace("’", "'")


# Context analysis --------------------------------------------------------------
class Expectation(NamedTuple):
    """Verb bits a word should have to fit the context (0: no expectation)."""

    bits: int
    reason: str


NONE = Expectation(0, "")

SUBJECTS = {
    "je": P1SG,
    "j'": P1SG,
    "tu": P2SG,
    "il": P3SG,
    "elle": P3SG,
    "on": P3SG,
    "nous": P1PL,
    "vous": P2PL,
    "ils": P3PL,
    "elles": P3PL,
}
# Words that may stand between the subject and the verb.
CLITICS = {
    "ne",
    "n'",
    "me",
    "m'",
    "te",
    "t'",
    "se",
    "s'",
    "le",
    "la",
    "les",
    "l'",
    "lui",
    "leur",
    "y",
    "en",
    "nous",
    "vous",
}
# Negation words that may stand between an auxiliary/modal and the verb.
ADVERBS = {"pas", "plus", "jamais", "rien", "point", "guère", "déjà", "bien", "tout"}
# Forms of avoir and être (compound tenses): a past participle follows.
AUXILIARIES = {
    # avoir
    "ai", "as", "a", "avons", "avez", "ont",
    "avais", "avait", "avions", "aviez", "avaient",
    "aurai", "auras", "aura", "aurons", "aurez", "auront",
    "aurais", "aurait", "aurions", "auriez", "auraient",
    "aie", "aies", "ait", "ayons", "ayez", "aient", "eu", "avoir", "ayant",
    # être
    "suis", "es", "est", "sommes", "êtes", "sont",
    "étais", "était", "étions", "étiez", "étaient",
    "serai", "seras", "sera", "serons", "serez", "seront",
    "serais", "serait", "serions", "seriez", "seraient",
    "sois", "soit", "soyons", "soyez", "soient", "été", "être", "étant",
}  # fmt: skip
# Words usually followed by an infinitive.
INFINITIVE_TRIGGERS = {
    # prepositions
    "pour", "sans", "afin", "avant",
    # aller, vouloir, pouvoir, devoir, savoir, falloir, laisser, faire, venir (de)
    "vais", "vas", "va", "allons", "allez", "vont", "aller", "allait", "allais",
    "veux", "veut", "voulons", "voulez", "veulent", "voulais", "voulait", "voudrais",
    "voudrait", "voudrions", "voudriez", "voudraient",
    "peux", "peut", "pouvons", "pouvez", "peuvent", "pouvais", "pouvait",
    "pourrais", "pourrait", "pourrions", "pourriez", "pourraient",
    "dois", "doit", "devons", "devez", "doivent", "devais", "devait", "devrais",
    "devrait", "devrions", "devriez", "devraient",
    "sais", "sait", "savons", "savez", "savent",
    "faut", "fallait", "faudra", "faudrait",
    "laisse", "laisser", "fais", "fait", "faire",
    "aime", "aimes", "aimons", "aimez", "aiment", "aimerais", "aimerait",
    "préfère", "préfères", "préférons", "préférez", "préfèrent",
}  # fmt: skip

_ELIDED = re.compile(r"^(jusqu|lorsqu|puisqu|quoiqu|qu|[cdjlmnst])'(.+)$")


def split_elisions(words: Sequence[str]) -> List[str]:
    """``["j'ai", "mang"]`` -> ``["j'", "ai", "mang"]`` (lowercase, ' only)."""
    out: List[str] = []
    for word in words:
        word = word.lower().replace("’", "'")
        match = _ELIDED.match(word)
        if match:
            out.extend((match.group(1) + "'", match.group(2)))
        else:
            out.append(word)
    return out


def expectation(context: Sequence[str]) -> Expectation:
    """What verb form fits after ``context`` (previous words, oldest first)."""
    words = split_elisions(context)
    i = len(words) - 1
    # Skip negation/adverbs right before the verb ("je n'ai pas mang").
    while i >= 0 and words[i] in ADVERBS:
        i -= 1
    if i >= 0 and words[i] in AUXILIARIES:
        return Expectation(PPAS, "auxiliary " + words[i])
    if i >= 0 and words[i] in INFINITIVE_TRIGGERS:
        return Expectation(INF, "before an infinitive: " + words[i])
    # Subject pronoun, possibly followed by object pronouns / ne.
    subject: Optional[int] = None
    while i >= 0:
        word = words[i]
        if word in ("nous", "vous"):
            # Subject ("nous mangeons") or object ("je vous parle"): look
            # further back for another subject.
            subject = SUBJECTS[word]
            i -= 1
            continue
        if word in SUBJECTS:
            return Expectation(SUBJECTS[word], "subject " + word)
        if word in CLITICS:
            i -= 1
            continue
        break
    if subject is not None:
        return Expectation(subject, "subject nous/vous")
    return NONE


def agreement_group(bits: int, expected: Expectation) -> int:
    """Sort group of a word: 0 fits, 1 neutral, 2 conflicts with the context."""
    if not expected.bits:
        return 1
    if bits & expected.bits:
        return 0
    if expected.bits & PERSONS and bits & PERSONS:
        return 2  # a conjugated form for another person
    if expected.bits & (PPAS | INF) and bits & (PERSONS | PPRE):
        return 2  # a conjugated form where a participle/infinitive is expected
    return 1
