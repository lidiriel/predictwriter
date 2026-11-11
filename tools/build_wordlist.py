#!/usr/bin/env python3
"""Expand a Hunspell dictionary into ``extension/data/<lang>.words.tsv``.

Output: one ``word<TAB>frequency[<TAB>verb_bits<TAB>elision_bits]`` line per
word form, sorted by lookup key (see :func:`lookup_key`), which is the order
``WordList.load`` expects. The optional bit masks (``predictive.grammar``)
come from the Hunspell morphological tags and elision prefix flags.

The affix expander below covers what the fr_FR and en_US dictionaries use:
``FLAG`` (char, long, num, UTF-8), ``SFX``/``PFX`` with conditions,
continuation classes (twofold suffixes, prefix+suffix cross products),
``NEEDAFFIX``, ``FORBIDDENWORD``, ``ONLYINCOMPOUND``, ``NOSUGGEST`` and
``CIRCUMFIX`` (affixes needing a circumfix partner are skipped). Flag
aliases (``AF``) are not supported and raise an error.

French elision prefixes (``l'``, ``d'``, ``qu'``...) are dropped by default:
the engine strips the elision from what the user typed and completes the rest,
which keeps the list about five times smaller.

Optional ``--freq FILE``: ``word count`` per line (whitespace separated, e.g.
the FrequencyWords lists). Counts are summed per lookup key. Words without a
count get ``--default-freq``; dictionary roots (lemmas such as ``prédire``,
as opposed to generated forms such as ``prédisions``) get ``--root-bonus``
on top, which is the only ranking signal when no frequency list is given.

Usage::

    python tools/build_wordlist.py --lang fr_FR
    python tools/build_wordlist.py --lang en_US --freq en_50k.txt
"""

import argparse
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, NamedTuple, Optional, Sequence, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIC_DIR = Path("/usr/share/hunspell")
APOSTROPHES = ("'", "’")

# Share the key and merge rules with the runtime (pure Python, no uno).
sys.path.insert(0, str(ROOT / "extension" / "python" / "pythonpath"))
from predictive import grammar  # noqa: E402
from predictive.wordlist import lookup_key, merge_by_key  # noqa: E402


class Affix(NamedTuple):
    """One SFX/PFX rule line."""

    strip: str
    add: str
    cont: frozenset  # continuation flags
    cond: "re.Pattern[str]"
    cross: bool
    morph: Tuple[str, ...] = ()  # morphological tags, e.g. ("po:ipre", "po:1sg")


class AffFile:
    """The subset of a Hunspell .aff file needed for expansion."""

    def __init__(self) -> None:
        self.flag_mode = "char"
        self.sfx: Dict[str, List[Affix]] = defaultdict(list)
        self.pfx: Dict[str, List[Affix]] = defaultdict(list)
        self.needaffix: Optional[str] = None
        self.forbidden: Optional[str] = None
        self.onlyincompound: Optional[str] = None
        self.nosuggest: Optional[str] = None
        self.circumfix: Optional[str] = None
        # Prefix flag -> elided words it adds ({"L'": {"l'"}, "Q*": {...}}).
        self.elisions: Dict[str, Set[str]] = {}

    def parse_flags(self, text: str) -> List[str]:
        """Split a flag string according to the FLAG mode."""
        if self.flag_mode == "long":
            return [text[i : i + 2] for i in range(0, len(text) - 1, 2)]
        if self.flag_mode == "num":
            return [f for f in text.split(",") if f]
        return list(text)


def _condition_regex(cond: str, suffix: bool) -> "re.Pattern[str]":
    """Translate a Hunspell condition (``.``, ``[abc]``, ``[^abc]``, literals)."""
    out = []
    i = 0
    while i < len(cond):
        ch = cond[i]
        if ch == ".":
            out.append(".")
        elif ch == "[":
            end = cond.index("]", i + 1)
            body = cond[i + 1 : end]
            neg = body.startswith("^")
            if neg:
                body = body[1:]
            out.append("[" + ("^" if neg else "") + re.escape(body) + "]")
            i = end
        else:
            out.append(re.escape(ch))
        i += 1
    pattern = "".join(out)
    return re.compile("(?:" + pattern + ")$" if suffix else "^(?:" + pattern + ")")


def read_aff(path: Path) -> AffFile:
    """Parse the parts of an .aff file used by :func:`expand`."""
    aff = AffFile()
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    # First pass: FLAG must be known before flags are split.
    for line in lines:
        parts = line.split()
        if parts and parts[0] == "FLAG" and len(parts) > 1:
            aff.flag_mode = {"long": "long", "num": "num"}.get(parts[1], "char")
        if parts and parts[0] == "AF":
            raise NotImplementedError("flag aliases (AF) are not supported")
    single = {
        "NEEDAFFIX": "needaffix",
        "FORBIDDENWORD": "forbidden",
        "ONLYINCOMPOUND": "onlyincompound",
        "NOSUGGEST": "nosuggest",
        "CIRCUMFIX": "circumfix",
    }
    headers: Dict[Tuple[str, str], bool] = {}
    for line in lines:
        parts = line.split()
        if not parts or parts[0].startswith("#"):
            continue
        kind = parts[0]
        if kind in single and len(parts) > 1:
            setattr(aff, single[kind], parts[1])
        elif kind in ("SFX", "PFX") and len(parts) >= 4:
            flag = parts[1]
            if (kind, flag) not in headers:
                # Header line: SFX flag cross count
                headers[(kind, flag)] = parts[2] == "Y"
                continue
            strip = "" if parts[2] == "0" else parts[2]
            add, _, cont = parts[3].partition("/")
            add = "" if add == "0" else add
            cond = parts[4] if len(parts) > 4 else "."
            rule = Affix(
                strip=strip,
                add=add,
                cont=frozenset(aff.parse_flags(cont)),
                cond=_condition_regex(cond, suffix=kind == "SFX"),
                cross=headers[(kind, flag)],
                morph=tuple(parts[5:]),
            )
            (aff.sfx if kind == "SFX" else aff.pfx)[flag].append(rule)
    for flag, rules in aff.pfx.items():
        added = set()
        for rule in rules:
            add = rule.add.replace("\u2019", "'")
            if "'" in add:
                added.add(add[: add.index("'") + 1].lower())
        added &= set(grammar.ELISION_BIT)
        if added:
            aff.elisions[flag] = added
    return aff


def read_dic(
    path: Path, aff: AffFile
) -> Iterable[Tuple[str, Set[str], Tuple[str, ...]]]:
    """Yield ``(word, flags, morphological tags)`` for each dictionary entry."""
    with path.open(encoding="utf-8", errors="replace") as fh:
        next(fh, None)  # entry count
        for line in fh:
            fields = line.split()
            entry = fields[0] if fields else ""
            if not entry or entry.startswith("#"):
                continue
            # Separator is the first '/' not escaped with a backslash.
            match = re.match(r"((?:\\/|[^/])+)(?:/(.*))?$", entry)
            if not match:
                continue
            word = match.group(1).replace("\\/", "/")
            flags = set(aff.parse_flags(match.group(2) or ""))
            yield word, flags, tuple(fields[1:])


def _apply_sfx(word: str, rule: Affix) -> Optional[str]:
    if not word.endswith(rule.strip) or not rule.cond.search(word):
        return None
    base = word[: len(word) - len(rule.strip)] if rule.strip else word
    return base + rule.add


def _apply_pfx(word: str, rule: Affix) -> Optional[str]:
    if not word.startswith(rule.strip) or not rule.cond.search(word):
        return None
    return rule.add + word[len(rule.strip) :]


def expand(
    word: str,
    flags: Set[str],
    aff: AffFile,
    keep_elisions: bool,
    tags: Tuple[str, ...] = (),
) -> Dict[str, List[int]]:
    """All word forms generated by one dictionary entry.

    Returns ``{form: [verb_bits, elision_bits]}`` (see ``predictive.grammar``):
    verb bits from the morphological tags of the rule producing the form,
    elision bits from the elision prefix flags allowed on it (only for forms
    starting with a vowel or an h).
    """
    blocked = {aff.forbidden, aff.onlyincompound, aff.nosuggest} - {None}
    if flags & blocked:
        return {}
    forms: Dict[str, List[int]] = {}

    def usable_pfx(rule: Affix) -> bool:
        if aff.circumfix and aff.circumfix in rule.cont:
            return False
        return keep_elisions or not any(a in rule.add for a in APOSTROPHES)

    def emit(form: str, cont: Iterable[str], morph: Iterable[str], eflags) -> None:
        if aff.needaffix in cont or aff.forbidden in cont:
            return
        bits = forms.setdefault(form, [0, 0])
        bits[0] |= grammar.verb_bits(morph)
        if grammar.can_elide(form):
            for flag in eflags:
                for elided in aff.elisions.get(flag, ()):
                    bits[1] |= grammar.ELISION_BIT[elided]

    def elision_flags(cont: Iterable[str]) -> Set[str]:
        return {f for f in cont if f in aff.elisions}

    root_eflags = elision_flags(flags)
    if aff.needaffix not in flags:
        emit(word, (), tags, root_eflags)

    prefix_flags = [f for f in flags if f in aff.pfx]
    for flag in flags:
        for sfx in aff.sfx.get(flag, ()):
            if aff.circumfix and aff.circumfix in sfx.cont:
                continue
            form = _apply_sfx(word, sfx)
            if form is None:
                continue
            eflags = elision_flags(sfx.cont) | (root_eflags if sfx.cross else set())
            emit(form, sfx.cont, sfx.morph, eflags)
            # Twofold suffixes: suffix classes in the continuation flags.
            for flag2 in sfx.cont:
                for sfx2 in aff.sfx.get(flag2, ()):
                    form2 = _apply_sfx(form, sfx2)
                    if form2 is not None:
                        emit(
                            form2,
                            sfx2.cont,
                            sfx2.morph or sfx.morph,
                            elision_flags(sfx2.cont),
                        )
            # Prefix + suffix cross products.
            if sfx.cross:
                for pflag in set(prefix_flags) | (sfx.cont & aff.pfx.keys()):
                    for pfx in aff.pfx[pflag]:
                        if pfx.cross and usable_pfx(pfx):
                            pform = _apply_pfx(form, pfx)
                            if pform is not None:
                                cont = pfx.cont | sfx.cont
                                emit(pform, cont, sfx.morph, elision_flags(cont))

    for pflag in prefix_flags:
        for pfx in aff.pfx[pflag]:
            if not usable_pfx(pfx):
                continue
            pform = _apply_pfx(word, pfx)
            if pform is None:
                continue
            emit(pform, pfx.cont, pfx.morph or tags, elision_flags(pfx.cont))
            # Suffixes listed in the prefix continuation (e.g. ``0 0/S.``).
            for flag2 in pfx.cont:
                for sfx in aff.sfx.get(flag2, ()):
                    if pfx.cross and sfx.cross:
                        sform = _apply_sfx(pform, sfx)
                        if sform is not None:
                            emit(sform, sfx.cont, sfx.morph, elision_flags(sfx.cont))
    return forms


def read_freq(path: Path) -> Dict[str, int]:
    """Read ``word count`` lines; counts are summed per lookup key."""
    freq: Dict[str, int] = defaultdict(int)
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.split()
            if len(parts) >= 2 and parts[-1].isdigit():
                freq[lookup_key(" ".join(parts[:-1]))] += int(parts[-1])
    return freq


def build(
    dic: Path,
    aff_path: Path,
    freq_path: Optional[Path],
    min_length: int,
    default_freq: int,
    keep_elisions: bool,
    root_bonus: int = 1,
) -> List[Tuple[str, int, int, int]]:
    """Expand the dictionary; rows are ``(word, freq, verb_bits, elision_bits)``."""
    aff = read_aff(aff_path)
    words: Dict[str, List[int]] = {}
    roots: Set[str] = set()
    for word, flags, tags in read_dic(dic, aff):
        forms = expand(word, flags, aff, keep_elisions, tags)
        for form, (verb, elision) in forms.items():
            bits = words.setdefault(form, [0, 0])
            bits[0] |= verb
            bits[1] |= elision
        if word in forms:
            roots.add(word)
    freq = read_freq(freq_path) if freq_path else {}
    rows = [
        (
            w,
            freq.get(lookup_key(w), default_freq) + (root_bonus if w in roots else 0),
            bits[0],
            bits[1],
        )
        for w, bits in words.items()
        if len(w) >= min_length and "\t" not in w
    ]
    # One line per lookup key, sorted: WordList.load can then stream the file.
    best = merge_by_key(rows)
    return [best[key] for key in sorted(best)]


def elision_pairs(
    model: Path,
    rows: Sequence[Tuple[str, int, int, int]],
    freq: Dict[str, int],
    per_elision: int = 3000,
) -> List[Tuple[str, str, int]]:
    """Frequent elided pairs (``l'année``) from an n-gram model's unigrams.

    Score = min(count of the pair in the model, frequency of the word in the
    ``--freq`` list): the minimum damps pairs inflated by repeated template
    text in Wikipedia ("en l'améliorant"). Only pairs allowed by the
    dictionary's elision bits are kept. Returns ``(elision, word, score)``.
    """
    import sqlite3

    allowed = {lookup_key(w): (w, e) for w, _, _, e in rows if e}
    con = sqlite3.connect("file:%s?mode=ro" % model, uri=True)
    result: List[Tuple[str, str, int]] = []
    try:
        for elided, bit in grammar.ELISION_BIT.items():
            pairs = []
            query = "SELECT word, count FROM _1_gram WHERE word >= ? AND word < ?"
            for token, count in con.execute(query, (elided, elided + "\U0010ffff")):
                stem = token[len(elided) :]
                entry = allowed.get(lookup_key(stem))
                if entry is None or not entry[1] & bit:
                    continue
                score = min(count, freq.get(lookup_key(stem), 0))
                if score >= 2:
                    pairs.append((score, entry[0]))
            pairs.sort(key=lambda p: (-p[0], p[1]))
            result.extend((elided, word, score) for score, word in pairs[:per_elision])
    finally:
        con.close()
    return result


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lang", required=True, help="e.g. fr_FR, en_US")
    parser.add_argument("--dic-dir", type=Path, default=DEFAULT_DIC_DIR)
    parser.add_argument("--freq", type=Path, help="optional 'word count' list")
    # Short words matter after an elision ("j'" -> "ai") and as next words.
    parser.add_argument("--min-length", type=int, default=1)
    parser.add_argument("--default-freq", type=int, default=0)
    parser.add_argument("--root-bonus", type=int, default=1)
    parser.add_argument("--keep-elisions", action="store_true")
    parser.add_argument(
        "--elision-model",
        type=Path,
        help="n-gram model (tools/train_ngrams.py) giving the frequency of "
        "elided pairs such as l'année; writes <lang>.elisions.tsv",
    )
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    out = args.out or ROOT / "extension" / "data" / ("%s.words.tsv" % args.lang)
    start = time.monotonic()
    rows = build(
        args.dic_dir / (args.lang + ".dic"),
        args.dic_dir / (args.lang + ".aff"),
        args.freq,
        args.min_length,
        args.default_freq,
        args.keep_elisions,
        args.root_bonus,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as fh:
        for word, count, verb, elision in rows:
            if verb or elision:
                fh.write("%s\t%d\t%d\t%d\n" % (word, count, verb, elision))
            else:
                fh.write("%s\t%d\n" % (word, count))
    if args.elision_model:
        freq = read_freq(args.freq) if args.freq else {}
        pairs = elision_pairs(args.elision_model, rows, freq)
        pairs_out = out.with_name("%s.elisions.tsv" % args.lang)
        with pairs_out.open("w", encoding="utf-8", newline="\n") as fh:
            for elided, word, score in pairs:
                fh.write("%s\t%s\t%d\n" % (elided, word, score))
        print("wrote %s (%d pairs)" % (pairs_out, len(pairs)), file=sys.stderr)
    print(
        "wrote %s (%d words, %.1fs)" % (out, len(rows), time.monotonic() - start),
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
