"""User settings stored as JSON (plain Python, no ``uno``)."""

import json
import logging
import os
from typing import Any, Dict

from predictive.logsetup import LOGGER_NAME

log = logging.getLogger(LOGGER_NAME + ".settings")

DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "language": "fr_FR",  # Phase 2 will read the language from CharLocale
    "min_prefix": 3,  # letters typed before suggesting
    # Letters a suggestion must add. 1: "je mang" offers "mange", and
    # "j'ai mang" offers "mangé" (one key for the accented letter).
    "min_completion": 1,
    "max_suggestions": 6,  # words visible at once in the list (1-9: Alt+1...Alt+9)
    # Words reachable by scrolling the list with Up/Down and Page Up/Down
    # (1-100). Larger values cost a little more per key (about 0.1 ms per 10
    # words for the engine); the list itself only draws the visible rows.
    "max_candidates": 30,
    "list_position": "cursor",  # cursor | bottom | top | none
    # Also show the highlighted word in the text, as selected letters
    # ("prédi[re]"). Off by default for speed: every change the extension
    # makes to the text costs LibreOffice 15-60 ms (about 40 ms to show the
    # letters, 20 ms to remove them, 40 ms per Up/Down), measured in
    # LibreOffice 25.2, whereas the list alone takes about 5 ms per key.
    "inline": False,
    # Context prediction with the n-gram model built by tools/train_ngrams.py
    # (<profile>/predictive-writer/ngrams_<language>.sqlite). Off by default.
    "context_prediction": False,  # rank completions by the previous words
    "next_word": False,  # predict the next word after a space
    # Keys that accept a suggestion: "tab_enter", "tab" or "enter". After a
    # space, Enter usually means "new paragraph", hence Tab only by default.
    "accept_completion": "tab_enter",
    "accept_next_word": "tab",
    "debug": False,  # log every keystroke (contains typed words)
}


class Settings:
    """Dict-like settings backed by ``settings.json``; unknown keys are kept."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.values: Dict[str, Any] = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            log.exception("cannot read %s; using defaults", self.path)
            return
        if isinstance(data, dict):
            for key, value in data.items():
                default = DEFAULTS.get(key)
                if default is None or (
                    isinstance(value, type(default))
                    and isinstance(value, bool) == isinstance(default, bool)
                ):
                    self.values[key] = value

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.values, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)

    def __getitem__(self, key: str) -> Any:
        return self.values[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.values[key] = value
