"""Suggestion engine (plain Python, no ``uno``).

``Engine.suggest`` turns the word being typed into full-word suggestions. The
UNO layer only inserts ``suggestion[len(prefix):]``, so every suggestion
starts with exactly the characters the user typed.
"""

import logging
import os
import re
import threading
from typing import Dict, List, Optional, Sequence, Tuple

from predictive import grammar
from predictive.logsetup import LOGGER_NAME
from predictive.wordlist import WordList, lookup_key

log = logging.getLogger(LOGGER_NAME + ".engine")

DEFAULT_DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "data")
)

# French elisions: "l'arb" is completed as "l'" + complete("arb"). The word
# lists do not contain elided forms (see tools/build_wordlist.py).
SENTENCE_END = ".!?\u2026"

_ELISION_RE = {
    "fr": re.compile(
        r"^((?:jusqu|lorsqu|puisqu|quoiqu|qu|[cdjlmnst])['’])(.*)$",
        re.IGNORECASE,
    ),
}


class Engine:
    """Word completion over per-language word lists, loaded lazily."""

    def __init__(
        self,
        data_dir: str = DEFAULT_DATA_DIR,
        min_prefix: int = 3,
        max_suggestions: int = 5,
        min_completion: int = 1,
    ) -> None:
        self.data_dir = data_dir
        self.min_prefix = min_prefix
        self.min_completion = min_completion
        self.max_suggestions = max_suggestions
        self._lists: Dict[str, WordList] = {}
        self._loading: Dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    # Loading ---------------------------------------------------------------
    def wordlist_path(self, lang: str) -> str:
        return os.path.join(self.data_dir, "%s.words.tsv" % lang)

    def is_loaded(self, lang: str) -> bool:
        return lang in self._lists

    def preload(self, lang: str, background: bool = True) -> None:
        """Start loading ``lang`` (in a daemon thread by default). Idempotent."""
        with self._lock:
            if lang in self._lists or lang in self._loading:
                return
            thread = threading.Thread(
                target=self._load, args=(lang,), name="pw-load-" + lang, daemon=True
            )
            self._loading[lang] = thread
        if background:
            thread.start()
        else:
            thread.run()

    def _load(self, lang: str) -> None:
        path = self.wordlist_path(lang)
        try:
            wordlist = WordList.load(path)
            pairs = os.path.join(self.data_dir, "%s.elisions.tsv" % lang)
            if os.path.exists(pairs):
                wordlist.load_elision_pairs(pairs)
            wordlist.prepare_elisions()
        except OSError:
            log.error("cannot read word list %s", path)
            return  # keep it in _loading so we do not retry on every key
        except Exception:
            log.exception("failed to load word list %s", path)
            return
        self._lists[lang] = wordlist
        with self._lock:
            self._loading.pop(lang, None)
        log.info("loaded %s: %d words", lang, len(wordlist))

    def set_wordlist(self, lang: str, wordlist: WordList) -> None:
        """Install a word list directly (tests, custom sources)."""
        self._lists[lang] = wordlist

    # Suggestions -----------------------------------------------------------
    def suggest(
        self,
        prefix: str,
        context: Sequence[str] = (),
        lang: str = "en_US",
        apostrophe: str = "'",
    ) -> List[str]:
        """Return completions for ``prefix``, best first.

        Args:
            prefix: The word fragment left of the cursor.
            context: Previous words (unused until next-word prediction).
            lang: Language tag such as ``fr_FR``; its word list loads lazily
                and no suggestions are returned until it is ready.
            apostrophe: Apostrophe to use in suggestions when the prefix
                contains none (e.g. ``’`` if the paragraph uses typographic
                apostrophes).

        Returns:
            Full words starting with ``prefix`` exactly as typed.
        """
        wordlist = self._lists.get(lang)
        if wordlist is None:
            self.preload(lang)
            return []
        if any(a in prefix for a in "'’"):
            apostrophe = "’" if "’" in prefix else "'"
        head, stem = self.split_elision(prefix, lang)
        elision_bit = 0
        if head and wordlist.has_grammar:
            elision_bit = grammar.ELISION_BIT.get(grammar.elision_key(head), 0)
        # After an elision with grammar data ("j'", "l'a"), suggest at once:
        # the elision itself narrows the candidates.
        if len(stem) < self.min_prefix and not elision_bit:
            return []
        upper = len(stem) >= 2 and stem.isupper()
        expected = self.expectation(context, head, wordlist)
        # The agreeing forms are fetched separately below, so a small pool
        # of the most frequent words is enough for the rest.
        pool_size = self.max_suggestions * 3
        pool = wordlist.candidates(stem, pool_size, elision_bit)
        if expected.bits:
            # The most frequent agreeing forms, even if rarer than the rest.
            agreeing = wordlist.candidates(
                stem, self.max_suggestions, elision_bit, expected.bits
            )
            first = set(agreeing)
            pool = agreeing + [i for i in pool if i not in first]
            pool.sort(
                key=lambda i: grammar.agreement_group(wordlist.verb_bits(i), expected)
            )  # stable: frequency order kept within each group
        results: List[str] = []
        seen = set()
        for index in pool:
            word = wordlist.word(index)
            rest = word[len(stem) :]
            if stem and len(rest) < self.min_completion:
                continue
            # Acronyms / mixed case ("ANTAI", "McDonald"): only when the typed
            # part already has the dictionary's case (first letter aside).
            if not upper and word[1:] != word[1:].lower():
                if word[1 : len(stem)] != stem[1:]:
                    continue
            if upper:
                rest = rest.upper()
            rest = rest.replace("'", apostrophe).replace("’", apostrophe)
            full = head + stem + rest
            key = lookup_key(full)
            if key not in seen:
                seen.add(key)
                results.append(full)
                if len(results) >= self.max_suggestions:
                    break
        return results

    def completes(self, prefix: str, lang: str) -> bool:
        """True if ``prefix`` is long enough to be completed.

        At least ``min_prefix`` letters, or any elided word ("j'", "l'a")
        when the word list has grammar data.
        """
        head, stem = self.split_elision(prefix, lang)
        if len(stem) >= self.min_prefix:
            return True
        wordlist = self._lists.get(lang)
        return bool(head) and wordlist is not None and wordlist.has_grammar

    def split_elision(self, prefix: str, lang: str) -> Tuple[str, str]:
        """``"l'arb"`` -> ``("l'", "arb")``; ``("", prefix)`` if no elision."""
        elision = _ELISION_RE.get(lang[:2])
        match = elision.match(prefix) if elision else None
        if match:
            return match.group(1), match.group(2)
        return "", prefix

    @staticmethod
    def expectation(
        context: Sequence[str], head: str, wordlist: Optional[WordList]
    ) -> grammar.Expectation:
        """Grammatical expectation for the word being typed (French data only)."""
        if wordlist is None or not wordlist.has_grammar:
            return grammar.NONE
        return grammar.expectation(list(context) + ([head] if head else []))

    def merge_context(
        self,
        prefix: str,
        model_words: Sequence[str],
        dictionary_words: Sequence[str],
        lang: str = "en_US",
        apostrophe: str = "'",
        sentence_start: bool = False,
        context: Sequence[str] = (),
    ) -> List[str]:
        """Combine context-model words (first) with dictionary suggestions.

        Args:
            prefix: The word fragment left of the cursor ('' for next word).
            model_words: Lowercase words from the n-gram model, best first.
            dictionary_words: Result of :meth:`suggest` (already formatted).
            lang: Language of the word list used to check and spell words.
            apostrophe: Apostrophe style when the prefix contains none.
            sentence_start: Capitalise next-word predictions.
            context: Previous words, for verb agreement (French).

        Returns:
            Up to ``max_suggestions`` words starting with ``prefix`` as typed.
            Model words must be in the word list (and accept the typed
            elision); dictionary spelling is used ("paris" -> "Paris"). Words
            agreeing with the context (verb person, participle...) come first.
        """
        wordlist = self._lists.get(lang)
        if any(a in prefix for a in "'\u2019"):
            apostrophe = "\u2019" if "\u2019" in prefix else "'"
        elision = _ELISION_RE.get(lang[:2])
        prefix_key = lookup_key(prefix)
        upper = len(prefix) >= 2 and prefix.isupper()
        results: List[str] = []
        seen = set()

        def add(word: str) -> None:
            key = lookup_key(word)
            if key not in seen and len(results) < self.max_suggestions:
                seen.add(key)
                results.append(word)

        for word in model_words:
            if not lookup_key(word).startswith(prefix_key):
                continue
            if prefix and len(word) - len(prefix) < self.min_completion:
                continue
            head, stem = "", word
            match = elision.match(word) if elision else None
            if match:
                head, stem = match.group(1), match.group(2)
            index = wordlist.index_of(stem) if wordlist is not None and stem else None
            if index is None:
                continue  # unknown word (foreign word, typo...)
            if head and wordlist.has_grammar:
                bit = grammar.ELISION_BIT.get(grammar.elision_key(head), 0)
                if bit and not wordlist.elision_bits(index) & bit:
                    continue  # "l'arbres"
            full_word = head + wordlist.word(index)
            if prefix:
                rest = full_word[len(prefix) :]
                if upper:
                    rest = rest.upper()
                full = prefix + rest.replace("'", apostrophe)
            else:
                full = full_word.replace("'", apostrophe)
                if sentence_start:
                    full = full[:1].upper() + full[1:]
            add(full)
        for word in dictionary_words:
            add(word)
        # Verb agreement: stable re-ordering, context order kept in each group.
        typed_head, _ = self.split_elision(prefix, lang)
        expected = self.expectation(context, typed_head, wordlist)
        if expected.bits and wordlist is not None:

            def group(word: str) -> int:
                _, stem = self.split_elision(word, lang)
                index = wordlist.index_of(stem)
                bits = wordlist.verb_bits(index) if index is not None else 0
                return grammar.agreement_group(bits, expected)

            results.sort(key=group)
        return results
