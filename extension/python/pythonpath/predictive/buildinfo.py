"""Version and build information (plain Python, no ``uno``).

``tools/build_oxt.py`` writes ``buildinfo.json`` next to this module inside
the ``.oxt`` (it is not in the source tree): the version, and the last git
commit (short hash, date, whether the working tree had uncommitted changes).
"""

import json
import os
from typing import Dict, Optional

from predictive import __version__

BUILDINFO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "buildinfo.json")


def load(path: Optional[str] = None) -> Dict[str, object]:
    """Build information, or ``{}`` when running from the source tree."""
    try:
        with open(path or BUILDINFO, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def describe(info: Optional[Dict[str, object]] = None) -> str:
    """One line such as ``Predictive Writer 1.0 — commit 04ab2b9 of 2026-11-11 20:35``."""
    info = load() if info is None else info
    version = info.get("version") or __version__
    text = "Predictive Writer %s" % version
    if not info:
        return text + " — development version (not packaged)"
    commit, date = info.get("commit"), str(info.get("commit_date") or "")
    if commit:
        # ISO 8601 "2026-11-11T20:35:10+01:00" -> "2026-11-11 20:35"
        when = date[:16].replace("T", " ") if date else "?"
        text += " — commit %s of %s" % (commit, when)
        if info.get("modified"):
            text += " (with uncommitted changes)"
    elif info.get("built"):
        text += " — built %s" % str(info["built"])[:16].replace("T", " ")
    return text
