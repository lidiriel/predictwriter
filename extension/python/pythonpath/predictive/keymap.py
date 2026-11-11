"""What a key does while suggestions are shown (plain Python, no ``uno``).

Key codes are the values of the ``com.sun.star.awt.Key`` and
``com.sun.star.awt.KeyModifier`` constants (checked with pyuno).
"""

from typing import FrozenSet, NamedTuple, Optional

KEY_NUM1 = 257  # Key.NUM1 ... Key.NUM9 = 265
KEY_NUM9 = 265
KEY_DOWN = 1024
KEY_UP = 1025
KEY_PAGEUP = 1030
KEY_PAGEDOWN = 1031
KEY_RETURN = 1280
KEY_ESCAPE = 1281
KEY_TAB = 1282
KEY_BACKSPACE = 1283
KEY_DELETE = 1286

MOD_SHIFT = 1
MOD_CTRL = 2  # KeyModifier.MOD1
MOD_ALT = 4  # KeyModifier.MOD2

# Accept-key settings ("accept_completion", "accept_next_word").
ACCEPT_KEY_CHOICES = ("tab_enter", "tab", "enter")
ACCEPT_KEY_LABELS = ("Tab or Enter", "Tab only", "Enter only")


def accept_codes(choice: str) -> FrozenSet[int]:
    """Key codes that accept a suggestion for a setting value."""
    if choice == "tab":
        return frozenset((KEY_TAB,))
    if choice == "enter":
        return frozenset((KEY_RETURN,))
    return frozenset((KEY_TAB, KEY_RETURN))


# Actions
ACCEPT = "accept"  # take the highlighted word
PICK = "pick"  # take the word at ``index``
MOVE = "move"  # move the highlight by ``delta`` (wraps around)
PAGE = "page"  # move the highlight by ``delta`` pages (stops at the ends)
REJECT = "reject"  # remove the suggestion
KEEP = "keep"  # ignore the key, keep everything as is


class KeyAction(NamedTuple):
    """Decision for one key press while suggestions are pending."""

    action: str
    consume: bool  # True: Writer must not see the key
    index: Optional[int] = None  # for PICK: position among the visible rows
    delta: int = 0  # for MOVE and PAGE


def action_for_key(
    code: int,
    mods: int,
    char: str,
    n_words: int,
    inline: bool,
    list_shown: bool,
    n_visible: Optional[int] = None,
    accept: FrozenSet[int] = frozenset((KEY_TAB, KEY_RETURN)),
) -> KeyAction:
    """Decide what a key does while suggestions are pending.

    Args:
        code: ``KeyEvent.KeyCode``.
        mods: ``KeyEvent.Modifiers``.
        char: ``KeyEvent.KeyChar`` ('' if none).
        n_words: Number of suggestions (including those reached by scrolling).
        inline: True if the highlighted word's letters are shown in the text.
        list_shown: True if the suggestion list is visible.
        n_visible: Rows currently visible in the list (default: ``n_words``);
            Alt+1...Alt+9 pick among them.
        accept: Key codes that accept the highlighted word (Tab and/or
            Enter); the other one closes the list and reaches Writer.
    """
    if n_visible is None:
        n_visible = n_words
    if mods == 0 and code in accept:
        return KeyAction(ACCEPT, True)
    if mods == 0 and code == KEY_ESCAPE:
        return KeyAction(REJECT, True)
    if mods == 0 and code in (KEY_BACKSPACE, KEY_DELETE):
        # With inline letters selected, the key only removes them (as it
        # would remove any selection); without them it edits the text.
        return KeyAction(REJECT, inline)
    if mods == 0 and code in (KEY_DOWN, KEY_UP) and list_shown and n_words > 1:
        return KeyAction(MOVE, True, delta=1 if code == KEY_DOWN else -1)
    if mods == 0 and code in (KEY_PAGEDOWN, KEY_PAGEUP) and list_shown and n_words > 1:
        return KeyAction(PAGE, True, delta=1 if code == KEY_PAGEDOWN else -1)
    if mods == MOD_ALT and KEY_NUM1 <= code <= KEY_NUM9 and list_shown:
        index = code - KEY_NUM1
        if index < n_visible:
            return KeyAction(PICK, True, index=index)
    if code == 0 and not char:
        return KeyAction(KEEP, False)  # modifier-only event
    return KeyAction(REJECT, False)
