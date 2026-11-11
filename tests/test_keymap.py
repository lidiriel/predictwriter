"""Tests for predictive.keymap."""

import pytest

from predictive import keymap as k


def act(code=0, mods=0, char="", n=6, inline=True, shown=True):
    return k.action_for_key(code, mods, char, n, inline, shown)


def test_accept_keys():
    assert act(k.KEY_TAB) == k.KeyAction(k.ACCEPT, True)
    assert act(k.KEY_RETURN) == k.KeyAction(k.ACCEPT, True)
    # Shift+Enter, Ctrl+Tab... are not accept keys
    assert act(k.KEY_RETURN, k.MOD_SHIFT).action == k.REJECT
    assert act(k.KEY_TAB, k.MOD_CTRL) == k.KeyAction(k.REJECT, False)


def test_escape_and_backspace():
    assert act(k.KEY_ESCAPE) == k.KeyAction(k.REJECT, True)
    assert act(k.KEY_BACKSPACE, inline=True) == k.KeyAction(k.REJECT, True)
    # Without inline letters, Backspace must still delete a character.
    assert act(k.KEY_BACKSPACE, inline=False) == k.KeyAction(k.REJECT, False)


def test_arrows_move_only_when_list_shown():
    assert act(k.KEY_DOWN) == k.KeyAction(k.MOVE, True, delta=1)
    assert act(k.KEY_UP) == k.KeyAction(k.MOVE, True, delta=-1)
    assert act(k.KEY_DOWN, shown=False) == k.KeyAction(k.REJECT, False)
    assert act(k.KEY_DOWN, n=1) == k.KeyAction(k.REJECT, False)


@pytest.mark.parametrize("digit", range(1, 7))
def test_alt_digit_picks(digit):
    code = k.KEY_NUM1 + digit - 1
    assert act(code, k.MOD_ALT) == k.KeyAction(k.PICK, True, index=digit - 1)


def test_alt_digit_out_of_range_or_hidden():
    assert act(k.KEY_NUM1 + 6, k.MOD_ALT, n=6).action == k.REJECT  # Alt+7, 6 words
    assert act(k.KEY_NUM1, k.MOD_ALT, shown=False).action == k.REJECT
    assert act(k.KEY_NUM1, k.MOD_ALT | k.MOD_SHIFT).action == k.REJECT


def test_typing_and_modifiers():
    assert act(0, 0, "a") == k.KeyAction(k.REJECT, False)
    assert act(0, k.MOD_SHIFT, "") == k.KeyAction(k.KEEP, False)


def test_page_keys():
    assert act(k.KEY_PAGEDOWN) == k.KeyAction(k.PAGE, True, delta=1)
    assert act(k.KEY_PAGEUP) == k.KeyAction(k.PAGE, True, delta=-1)
    assert act(k.KEY_PAGEDOWN, shown=False) == k.KeyAction(k.REJECT, False)


def test_alt_digit_counts_visible_rows():
    # 30 words, 6 visible: Alt+6 is fine, Alt+7 is not.
    assert k.action_for_key(k.KEY_NUM1 + 5, k.MOD_ALT, "", 30, False, True, 6) == (
        k.KeyAction(k.PICK, True, index=5)
    )
    assert k.action_for_key(
        k.KEY_NUM1 + 6, k.MOD_ALT, "", 30, False, True, 6
    ).action == (k.REJECT)


def test_accept_key_settings():
    tab_only = k.accept_codes("tab")
    assert (
        k.action_for_key(k.KEY_TAB, 0, "", 6, False, True, accept=tab_only).action
        == k.ACCEPT
    )
    # Enter is not an accept key: the list closes and Writer gets the Enter.
    assert k.action_for_key(k.KEY_RETURN, 0, "", 6, False, True, accept=tab_only) == (
        k.KeyAction(k.REJECT, False)
    )
    enter_only = k.accept_codes("enter")
    assert k.action_for_key(k.KEY_TAB, 0, "", 6, False, True, accept=enter_only) == (
        k.KeyAction(k.REJECT, False)
    )
    assert k.accept_codes("tab_enter") == {k.KEY_TAB, k.KEY_RETURN}
    assert k.accept_codes("bogus") == {k.KEY_TAB, k.KEY_RETURN}
    assert len(k.ACCEPT_KEY_CHOICES) == len(k.ACCEPT_KEY_LABELS)
