"""Pure-Python helpers to extract the word being typed from raw text.

This module must never import ``uno``: it is unit-tested with pytest outside
LibreOffice.

We deliberately do not rely on Writer's own word boundaries
(``XWordCursor.gotoStartOfWord``) because they depend on the locale's break
iterator and treat apostrophes and hyphens inconsistently. Instead, the UNO
layer hands us the text between the start of the paragraph and the cursor,
and we split it here with rules we control.
"""

import re
from typing import List, Tuple

# Apostrophes considered part of a word: ASCII and typographic (U+2019),
# the latter being what LibreOffice's AutoCorrect inserts in French.
APOSTROPHES = "'’"
HYPHENS = "-‑"  # hyphen-minus and non-breaking hyphen

# A word: letters/digits, optionally joined by apostrophes or hyphens
# (``aujourd'hui``, ``peut-être``, ``l'arbre``). A trailing joiner is kept so
# that ``l'`` or ``peut-`` while typing are still recognised as a prefix.
_JOINERS = re.escape(APOSTROPHES + HYPHENS)
_WORD_RE = re.compile(r"[^\W_]+(?:[" + _JOINERS + r"][^\W_]+)*[" + _JOINERS + r"]?")
_TRAILING_WORD_RE = re.compile(r"(?:" + _WORD_RE.pattern + r")$")

# Only look at this many characters before the cursor: enough for a word and
# a few words of context, small enough to keep regex work negligible.
MAX_LOOKBEHIND = 200


def extract_context(
    text_before_cursor: str, max_context: int = 3
) -> Tuple[str, List[str]]:
    """Split the text before the cursor into (current prefix, previous words).

    Args:
        text_before_cursor: Text from the start of the paragraph to the cursor.
        max_context: Maximum number of previous words to return.

    Returns:
        A tuple ``(prefix, context)`` where ``prefix`` is the (possibly empty)
        word immediately left of the cursor and ``context`` holds up to
        ``max_context`` preceding words, oldest first.
    """
    text = text_before_cursor[-MAX_LOOKBEHIND:]
    match = _TRAILING_WORD_RE.search(text)
    if match:
        prefix = match.group(0)
        rest = text[: match.start()]
    else:
        prefix = ""
        rest = text
    context = _WORD_RE.findall(rest)[-max_context:] if max_context > 0 else []
    return prefix, context
