"""Tests for predictive.popup_layout."""

from predictive.popup_layout import GAP, Rect, find_window, place

AREA = Rect(20, 40, 800, 500)  # text area: x 20..820, y 40..540


def test_under_cursor():
    caret = Rect(300, 100, 0, 19)
    assert place("cursor", 150, 120, caret, AREA) == Rect(300, 119 + GAP, 150, 120)


def test_above_cursor_near_bottom():
    caret = Rect(300, 480, 0, 19)
    r = place("cursor", 150, 120, caret, AREA)
    assert r.y == 480 - GAP - 120


def test_stays_below_if_no_room_above_either():
    caret = Rect(300, 60, 0, 19)
    r = place("cursor", 150, 600, caret, AREA)
    assert r.y == 79 + GAP


def test_kept_inside_horizontally():
    caret = Rect(790, 100, 0, 19)
    assert place("cursor", 150, 120, caret, AREA).x == 820 - 150
    caret = Rect(5, 100, 0, 19)
    assert place("cursor", 150, 120, caret, AREA).x == 20


def test_bars():
    caret = Rect(300, 100, 0, 19)
    assert place("bottom", 400, 22, caret, AREA) == Rect(24, 540 - 22 - 4, 400, 22)
    assert place("top", 400, 22, caret, AREA) == Rect(24, 44, 400, 22)


class FakeWindow:
    """Duck-typed stand-in for an XWindow with XVclContainer."""

    def __init__(self, x, y, w, h, children=(), container=True):
        self._rect = Rect(x, y, w, h)
        self._children = list(children)
        self._container = container

    def getPosSize(self):
        class R:
            pass

        r = R()
        r.X, r.Y, r.Width, r.Height = self._rect
        return r

    def getWindows(self):
        if not self._container:
            raise AttributeError("getWindows")
        return self._children


def test_find_window_nested():
    edit = FakeWindow(10, 5, 800, 500, container=False)
    view = FakeWindow(0, 20, 830, 520, [FakeWindow(0, 0, 830, 5), edit])
    root = FakeWindow(0, 0, 1000, 700, [FakeWindow(0, 0, 1000, 20), view])
    found = find_window(root, Rect(11, 25, 799, 501))  # within tolerance
    assert found is not None and found[0] is edit and found[1] == (10, 25)


def test_find_window_missing():
    root = FakeWindow(0, 0, 1000, 700, container=False)
    assert find_window(root, Rect(0, 0, 10, 10)) is None
    root = FakeWindow(0, 0, 1000, 700, [FakeWindow(0, 0, 50, 50)])
    assert find_window(root, Rect(0, 26, 900, 600)) is None


def test_scroll_top():
    from predictive.popup_layout import scroll_top

    assert scroll_top(0, 3, 6, 30) == 0  # visible: no scroll
    assert scroll_top(0, 6, 6, 30) == 1  # one past the bottom: scroll by one
    assert scroll_top(10, 4, 6, 30) == 4  # above the top: scroll up
    assert scroll_top(0, 29, 6, 30) == 24  # wrap to the end
    assert scroll_top(24, 0, 6, 30) == 0  # wrap to the start
    assert scroll_top(0, 2, 6, 4) == 0  # fewer words than rows


def test_page_move():
    from predictive.popup_layout import page_move

    assert page_move(0, 1, 6, 30) == 6
    assert page_move(27, 1, 6, 30) == 29  # stops at the end
    assert page_move(3, -1, 6, 30) == 0  # stops at the start


def test_page_top():
    from predictive.popup_layout import page_top

    assert page_top(0, 1, 6, 30) == 6  # next page
    assert page_top(1, 1, 6, 30) == 7
    assert page_top(20, 1, 6, 30) == 24  # last page is full
    assert page_top(4, -1, 6, 30) == 0
    assert page_top(0, 1, 6, 4) == 0  # everything fits
