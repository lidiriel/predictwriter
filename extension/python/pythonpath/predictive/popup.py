"""Suggestion list window and cursor location (UNO).

The list is a *child* window, not a top-level window: it never takes the
keyboard focus, the window manager does not move it, and it works the same on
X11 and Wayland. It is clipped to the document area, where it belongs anyway.

Its parent is Writer's text area (edit window) when we can find it, not the
document's component window: as a *sibling* of the text area, the list was
partly overdrawn when Writer repainted the cursor line (selection highlight,
blinking cursor), whereas a parent does not paint over its children. The API
does not name the edit window, so we look among the component window's
sub-windows (``XVclContainer``) for the one whose bounds match the text area
reported by the accessibility API.

The cursor's pixel position is not available from the text API (the
controller exposes no visible-area/scroll information), so we use the
accessibility API, which screen readers rely on: the accessible paragraph
holding the caret gives character bounds in pixels.
"""

import logging
from typing import Any, Callable, List, Optional, Tuple

import uno
import unohelper
from com.sun.star.awt import XMouseListener

from predictive.logsetup import LOGGER_NAME
from predictive.popup_layout import Rect, find_window, is_vertical, place, scroll_top

log = logging.getLogger(LOGGER_NAME + ".popup")

POS_SIZE = 15  # com.sun.star.awt.PosSize.POSSIZE
MOUSE_LEFT = 1  # com.sun.star.awt.MouseButton.LEFT
MAX_ITEMS = 9  # Alt+1 ... Alt+9

COLOR_BACKGROUND = 0xFFFFFF
COLOR_TEXT = 0x202020
COLOR_SELECTED_BACKGROUND = 0x3584E4
COLOR_SELECTED_TEXT = 0xFFFFFF
COLOR_STATUS = 0x808080
PAD_X = 6
PAD_Y = 2
MAX_NODES = 2000  # safety limit when searching the accessibility tree


def _role(name: str) -> int:
    return uno.getConstantByName("com.sun.star.accessibility.AccessibleRole." + name)


class CaretLocator:
    """Finds the text cursor in pixels, relative to the component window."""

    def __init__(self, component_window: Any) -> None:
        self._window = component_window
        self._paragraph: Any = None  # cache: accessible paragraph with the caret
        self._doc_view: Any = None
        self._document_role = _role("DOCUMENT_TEXT")

    def locate(self) -> Optional[Tuple[Rect, Rect]]:
        """Return ``(caret, text_area)`` or None if the cursor is not found.

        ``caret`` is the cursor line (x of the cursor, top, line height);
        ``text_area`` the visible document area.
        """
        root = self._window.getAccessibleContext()
        origin = root.getLocationOnScreen()
        doc_view = self._document_view(root)
        if doc_view is None:
            return None
        loc = doc_view.getLocationOnScreen()
        size = doc_view.getSize()
        area = Rect(loc.X - origin.X, loc.Y - origin.Y, size.Width, size.Height)
        paragraph = self._caret_paragraph(doc_view)
        if paragraph is None:
            return None
        caret_index = paragraph.getCaretPosition()
        para_loc = paragraph.getLocationOnScreen()
        if paragraph.getCharacterCount() == 0:
            bounds = paragraph.getBounds()
            x, y, height = 0, 0, bounds.Height
        elif caret_index > 0:
            # Right edge of the character before the caret: it existed before
            # our inline letters were inserted, so its bounds are laid out.
            b = paragraph.getCharacterBounds(caret_index - 1)
            x, y, height = b.X + b.Width, b.Y, b.Height
        else:
            b = paragraph.getCharacterBounds(0)
            x, y, height = b.X, b.Y, b.Height
        caret = Rect(
            para_loc.X - origin.X + x, para_loc.Y - origin.Y + y, 0, max(height, 1)
        )
        return caret, area

    def _document_view(self, root: Any) -> Any:
        if self._doc_view is not None:
            return self._doc_view
        for node in _descendants(root):
            if node.getAccessibleRole() == self._document_role:
                self._doc_view = node
                return node
        return None

    def _caret_paragraph(self, doc_view: Any) -> Any:
        cached = self._paragraph
        if cached is not None:
            try:
                if cached.getCaretPosition() >= 0:
                    return cached
            except Exception:
                pass  # disposed (scrolled out, deleted...)
        self._paragraph = None
        for node in _descendants(doc_view):
            if hasattr(node, "getCaretPosition"):
                try:
                    if node.getCaretPosition() >= 0:
                        self._paragraph = node
                        return node
                except Exception:
                    continue
        return None


def _descendants(root: Any):
    """Breadth-first walk over accessible contexts (children before grandchildren)."""
    queue = [root]
    seen = 0
    while queue and seen < MAX_NODES:
        node = queue.pop(0)
        try:
            count = node.getAccessibleChildCount()
        except Exception:
            continue
        for i in range(min(count, MAX_NODES)):
            try:
                child = node.getAccessibleChild(i).getAccessibleContext()
            except Exception:
                continue
            seen += 1
            yield child
            queue.append(child)


class _ClickListener(unohelper.Base, XMouseListener):
    def __init__(self, index: int, on_pick: Callable[[int], None]) -> None:
        self._index = index
        self._on_pick = on_pick

    def mousePressed(self, event: Any) -> None:
        if event.Buttons == MOUSE_LEFT:
            self._on_pick(self._index)

    def mouseReleased(self, event: Any) -> None:
        pass

    def mouseEntered(self, event: Any) -> None:
        pass

    def mouseExited(self, event: Any) -> None:
        pass

    def disposing(self, source: Any) -> None:
        pass


class SuggestionPopup:
    """Numbered, scrollable list of suggestions drawn over the document area.

    ``rows`` words are visible at a time, numbered 1...rows (Alt+number picks
    them); moving the highlight past either end scrolls through all the words.
    """

    def __init__(
        self, ctx: Any, component_window: Any, on_pick: Callable[[int], None]
    ) -> None:
        self._ctx = ctx
        self._parent = component_window
        self._on_pick = on_pick  # called with an index into the full list
        self._box: Any = None
        self._labels: List[Any] = []
        self._status: Any = None  # "7-12 / 30" line, shown when scrollable
        self._visible = False
        self.locator = CaretLocator(component_window)
        # Position of the list's parent window in the component window.
        self._origin: Tuple[int, int] = (0, 0)
        # Current content (set by show()).
        self._words: List[str] = []
        self._rows = 1
        self._top = 0
        self._selected = 0
        self._position = ""
        self._caret = Rect(0, 0, 0, 0)
        self._area = Rect(0, 0, 0, 0)
        self._column_width = 0

    @property
    def visible(self) -> bool:
        return self._visible

    @property
    def top(self) -> int:
        """Index (in the full list) of the first visible row."""
        return self._top

    @property
    def visible_count(self) -> int:
        """Number of rows currently shown."""
        return max(0, min(self._rows, len(self._words) - self._top))

    def _create(self, area: Rect) -> None:
        found = find_window(self._parent, area)
        if found is not None:
            self._parent, self._origin = found
            log.info("suggestion list attached to the text area window")
        else:
            log.info(
                "text area window not found; suggestion list attached to the "
                "document window (it may be partly overdrawn near the cursor)"
            )
        smgr = self._ctx.getServiceManager()
        toolkit = smgr.createInstanceWithContext("com.sun.star.awt.Toolkit", self._ctx)
        model = smgr.createInstanceWithContext(
            "com.sun.star.awt.UnoControlContainerModel", self._ctx
        )
        model.BackgroundColor = COLOR_BACKGROUND
        model.Border = 1
        box = smgr.createInstanceWithContext(
            "com.sun.star.awt.UnoControlContainer", self._ctx
        )
        box.setModel(model)
        box.setVisible(False)
        box.createPeer(toolkit, self._parent)
        middle = uno.Enum("com.sun.star.style.VerticalAlignment", "MIDDLE")
        for i in range(MAX_ITEMS + 1):  # the last one is the status line
            label_model = smgr.createInstanceWithContext(
                "com.sun.star.awt.UnoControlFixedTextModel", self._ctx
            )
            label_model.VerticalAlign = middle
            label_model.NoLabel = True  # no "~" mnemonics
            label = smgr.createInstanceWithContext(
                "com.sun.star.awt.UnoControlFixedText", self._ctx
            )
            label.setModel(label_model)
            box.addControl("item%d" % i, label)
            label.setVisible(False)
            if i < MAX_ITEMS:
                label.addMouseListener(_ClickListener(i, self._clicked))
                self._labels.append(label)
            else:
                label_model.TextColor = COLOR_STATUS
                # Left-aligned: right-aligned text had its last glyph clipped.
                label_model.Align = 0
                self._status = label
        self._box = box

    def _clicked(self, row: int) -> None:
        self._on_pick(self._top + row)

    def show(self, words: List[str], selected: int, position: str, rows: int) -> bool:
        """Show ``words`` with ``selected`` highlighted, ``rows`` at a time.

        Returns False if the cursor could not be located (list not shown).
        """
        located = self.locator.locate()
        if located is None:
            self.hide()
            return False
        caret, area = located
        if self._box is None:
            self._create(area)
        # Coordinates relative to the list's parent window.
        ox, oy = self._origin
        self._caret = Rect(caret.x - ox, caret.y - oy, caret.width, caret.height)
        self._area = Rect(area.x - ox, area.y - oy, area.width, area.height)
        self._words = list(words)
        self._rows = max(1, min(rows, MAX_ITEMS))
        self._position = position
        self._top = 0
        self._selected = 0
        # Fixed column width for the whole list (so it does not jump while
        # scrolling): measured on the longest word.
        longest = max(self._words, key=len) if self._words else ""
        self._column_width = max(
            self._measure("%d  %s" % (self._rows, longest)),
            self._measure(self._status_text()) if self._scrollable() else 0,
        )
        self.select(selected)
        if not self._visible:
            self._box.setVisible(True)
            self._visible = True
        return True

    def select(self, index: int, top: Optional[int] = None) -> None:
        """Highlight word ``index`` of the full list, scrolling if needed.

        Args:
            top: First visible index to use (page turn); adjusted if needed
                so that ``index`` is visible.
        """
        if not self._words or self._box is None:
            return
        self._selected = max(0, min(index, len(self._words) - 1))
        start = self._top if top is None else top
        self._top = scroll_top(start, self._selected, self._rows, len(self._words))
        self._layout()

    def _scrollable(self) -> bool:
        return len(self._words) > self._rows

    def _status_text(self) -> str:
        last = self._top + self.visible_count
        return "%d\u2013%d / %d" % (self._top + 1, last, len(self._words))

    def _measure(self, text: str) -> int:
        """Preferred pixel width of ``text`` in a list row (padding included)."""
        probe = self._status
        probe.getModel().Label = text
        return probe.getPreferredSize().Width + 2 * PAD_X

    def _layout(self) -> None:
        count = self.visible_count
        sizes = []
        for row, label in enumerate(self._labels):
            if row < count:
                model = label.getModel()
                model.Label = "%d  %s" % (row + 1, self._words[self._top + row])
                selected = self._top + row == self._selected
                model.BackgroundColor = (
                    COLOR_SELECTED_BACKGROUND if selected else COLOR_BACKGROUND
                )
                model.TextColor = COLOR_SELECTED_TEXT if selected else COLOR_TEXT
                pref = label.getPreferredSize()
                sizes.append((pref.Width + 2 * PAD_X, pref.Height + 2 * PAD_Y))
            label.setVisible(row < count)
        if not sizes:
            return
        status = self._status_text() if self._scrollable() else ""
        self._status.getModel().Label = status
        self._status.setVisible(bool(status))
        row_h = max(h for _, h in sizes)
        if is_vertical(self._position):
            col_w = max([self._column_width] + [w for w, _ in sizes])
            for row, label in enumerate(self._labels[:count]):
                label.setPosSize(1, 1 + row * row_h, col_w, row_h, POS_SIZE)
            lines = count + (1 if status else 0)
            if status:
                self._status.setPosSize(1, 1 + count * row_h, col_w, row_h, POS_SIZE)
            width, height = col_w + 2, row_h * lines + 2
        else:
            x = 1
            for (w, _), label in zip(sizes, self._labels):
                label.setPosSize(x, 1, w, row_h, POS_SIZE)
                x += w
            if status:
                # Generous width: the measured width was a few pixels short.
                status_w = self._status.getPreferredSize().Width + 4 * PAD_X
                self._status.setPosSize(x, 1, status_w, row_h, POS_SIZE)
                x += status_w
            width, height = x + 1, row_h + 2
        rect = place(self._position, width, height, self._caret, self._area)
        self._box.setPosSize(rect.x, rect.y, rect.width, rect.height, POS_SIZE)

    def hide(self) -> None:
        if self._box is not None and self._visible:
            self._box.setVisible(False)
        self._visible = False

    def dispose(self) -> None:
        if self._box is not None:
            try:
                self._box.dispose()
            except Exception:
                log.debug("popup dispose failed (window already gone?)")
        self._box = None
        self._labels = []
        self._visible = False
