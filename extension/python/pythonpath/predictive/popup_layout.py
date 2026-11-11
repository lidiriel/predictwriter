"""Where to put the suggestion list (plain Python, no ``uno``).

All coordinates are pixels relative to the document's component window.
"""

from typing import Any, NamedTuple, Optional, Tuple

POSITION_CURSOR = "cursor"  # vertical list under (or above) the cursor
POSITION_BOTTOM = "bottom"  # horizontal bar at the bottom of the text area
POSITION_TOP = "top"  # horizontal bar at the top of the text area
POSITION_NONE = "none"  # no list (inline completion only)
POSITIONS = (POSITION_CURSOR, POSITION_BOTTOM, POSITION_TOP, POSITION_NONE)

# Pixels between the cursor line and the list. (A larger gap is not needed
# since the list is a child of the text area: Writer no longer paints the
# cursor or the selection highlight over it.)
GAP = 3
BAR_MARGIN = 4  # pixels between a bar and the text area's edge


class Rect(NamedTuple):
    x: int
    y: int
    width: int
    height: int


def scroll_top(top: int, selected: int, rows: int, total: int) -> int:
    """First visible index so that ``selected`` is visible, scrolling minimally.

    Args:
        top: Current first visible index.
        selected: Highlighted index.
        rows: Number of visible rows.
        total: Number of words.
    """
    rows = max(1, rows)
    if selected < top:
        top = selected
    elif selected >= top + rows:
        top = selected - rows + 1
    return max(0, min(top, max(0, total - rows)))


def page_move(index: int, pages: int, rows: int, total: int) -> int:
    """Index after moving ``pages`` pages of ``rows``; stops at both ends."""
    return max(0, min(total - 1, index + pages * max(1, rows)))


def page_top(top: int, pages: int, rows: int, total: int) -> int:
    """First visible index after turning ``pages`` pages (a full page turn)."""
    rows = max(1, rows)
    return max(0, min(top + pages * rows, max(0, total - rows)))


def is_vertical(position: str) -> bool:
    """True if the list is laid out as a column (one word per row)."""
    return position == POSITION_CURSOR


def place(position: str, width: int, height: int, caret: Rect, area: Rect) -> Rect:
    """Return the list rectangle.

    Args:
        position: One of ``POSITIONS`` (``none`` is treated as ``cursor``).
        width, height: Size of the list.
        caret: Cursor line: x of the cursor, top and height of the line.
        area: Visible text area; the list is kept inside it when possible.
    """
    if position == POSITION_BOTTOM:
        y = area.y + area.height - height - BAR_MARGIN
        return Rect(area.x + BAR_MARGIN, max(area.y, y), width, height)
    if position == POSITION_TOP:
        return Rect(area.x + BAR_MARGIN, area.y + BAR_MARGIN, width, height)
    # Under the cursor line; above it if there is no room below.
    y = caret.y + caret.height + GAP
    if y + height > area.y + area.height and caret.y - GAP - height >= area.y:
        y = caret.y - GAP - height
    x = min(caret.x, area.x + area.width - width)
    return Rect(max(area.x, x), y, width, height)


def find_window(
    root: Any, target: Rect, tolerance: int = 2
) -> Optional[Tuple[Any, Tuple[int, int]]]:
    """Find the sub-window of ``root`` covering ``target`` (root coordinates).

    Returns ``(window, (x, y))`` with the window's offset in ``root``, or None
    (e.g. if the windows do not support ``XVclContainer.getWindows``).
    """
    stack = [(root, 0, 0)]
    seen = 0
    while stack and seen < 500:
        window, ox, oy = stack.pop()
        try:
            children = window.getWindows()
        except Exception:
            continue  # not a container
        for child in children:
            seen += 1
            try:
                r = child.getPosSize()
            except Exception:
                continue
            x, y = ox + r.X, oy + r.Y
            if (
                abs(x - target.x) <= tolerance
                and abs(y - target.y) <= tolerance
                and abs(r.Width - target.width) <= tolerance
                and abs(r.Height - target.height) <= tolerance
            ):
                return child, (x, y)
            stack.append((child, x, y))
    return None
