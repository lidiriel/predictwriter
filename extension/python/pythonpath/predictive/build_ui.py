"""Texts and choices of the "Build model" dialog (plain Python, no ``uno``).

The dialog itself (controls, events) is in ``uno_glue.py``; this module turns
its choices into builder sources and the builder's events into short texts,
so that it can be tested without LibreOffice.
"""

from typing import Any, Dict, List, Optional, Tuple

from predictive import model_builder as mb

# Wikimedia projects in the dialog's list, in this order.
PROJECTS: Tuple[str, ...] = ("wikisource", "wikipedia", "wikinews", "wikiquote")
PROJECT_LABELS: Tuple[str, ...] = tuple(mb.WIKIMEDIA_PROJECTS[p][1] for p in PROJECTS)

LICENCE_NOTE = (
    "Wikimedia text is under CC BY-SA 4.0. It is read in 25 MB slices and "
    "never stored; the model is for your own use and records its source and "
    "licence."
)


def sources(
    use_folder: bool, folder: str, use_wikimedia: bool, project: str
) -> List[str]:
    """Builder sources ("folder:<path>", "wikisource"...): folder first."""
    result = []
    if use_folder and folder.strip():
        result.append("folder:" + folder.strip())
    if use_wikimedia and project in mb.WIKIMEDIA_PROJECTS:
        result.append(project)
    return result


def limits(max_download_mb: int, max_words_millions: int, min_count: int) -> Any:
    return mb.BuildLimits(
        max_download_bytes=int(max_download_mb) * mb.MB,
        max_words=int(max_words_millions) * 1_000_000,
        min_count=int(min_count),
    )


def _number(value: float) -> str:
    """12345678 -> "12.3 M", 45678 -> "45 700" (short, for a label)."""
    if value >= 1_000_000:
        return "%.1f M" % (value / 1e6)
    if value >= 10_000:
        value = round(value, -2)
    return "{:,}".format(int(value)).replace(",", " ")


def estimate_text(
    specs: List[str],
    language: str,
    max_download_mb: int,
    max_words_millions: int,
    min_count: int,
) -> str:
    """One or two lines shown before a build (no "~": it marks a shortcut key
    in LibreOffice labels)."""
    if not specs:
        return "Choose your documents and/or a Wikimedia project."
    built = [mb.parse_source(spec, language) for spec in specs]
    folders = [s for s in built if isinstance(s, mb.FolderSource)]
    for source in folders:
        if not source.folder.is_dir():
            return "Folder not found: %s" % source.folder
    est = mb.estimate(built, limits(max_download_mb, max_words_millions, min_count))
    unread = sum(s.pdf_files for s in folders if s.pdftotext is None)
    parts = ["About %s words" % _number(est["words"])]
    if est["download_mb"]:
        parts.append("%d MB to download" % est["download_mb"])
    parts.append("about %d min" % est["minutes"])
    parts.append("model about %d MB" % max(1, est["model_mb"]))
    text = ", ".join(parts) + ". Needs about %d MB free while building." % max(
        1, est["disk_mb"]
    )
    if unread:  # say it before the build, not only in the progress line
        return text + " %d PDF file(s) will be skipped: %s." % (
            unread,
            mb.PDFTOTEXT_MISSING.split(": ", 1)[1],
        )
    return text + " The new model replaces the current one."


def progress(event: Dict[str, Any], max_words: int) -> Tuple[str, Optional[int]]:
    """Status text and progress (0-100, or None to keep) for a builder event."""
    kind = event.get("event")
    if kind == "start":
        return "Starting...", 0
    if kind == "source":
        return "Reading %s..." % event.get("name", ""), None
    if kind == "file":
        return "Downloading %s..." % str(event.get("url", "")).rsplit("/", 1)[-1], None
    if kind == "download":
        done = float(event.get("downloaded", 0))
        budget = float(event.get("budget", 0)) or 1.0
        return (
            "%s: %.0f of %.0f MB downloaded"
            % (event.get("source", ""), done / mb.MB, budget / mb.MB),
            int(min(95, 95 * done / budget)),
        )
    if kind == "words":
        words = float(event.get("words", 0))
        return (
            "%s words read (%s words/s)"
            % (_number(words), _number(event.get("words_per_second", 0))),
            int(min(95, 95 * words / max(1, max_words))),
        )
    if kind == "skipped":
        if int(event.get("files", 1)) > 1:  # e.g. PDF files without pdftotext
            return str(event.get("reason", "")), None
        return "Skipped %s: %s" % (event.get("path", ""), event.get("reason", "")), None
    if kind == "finishing":
        return "Counting and indexing %s words..." % _number(event.get("words", 0)), 96
    if kind == "done":
        return "Installing the model...", 99
    if kind == "installed":
        text = "Done: model built from %s words in %.0f s, now in use." % (
            _number(event.get("words", 0)),
            float(event.get("seconds", 0)),
        )
        skipped = int(event.get("skipped", 0))
        if skipped:
            text += " %d file(s) could not be read (see log.txt)." % skipped
        return text, 100
    if kind == "cancelled":
        return "Cancelled; the previous model is kept.", 0
    if kind == "error":
        return "Error: %s" % event.get("message", "unknown"), 0
    return "", None


def finished(event: Optional[Dict[str, Any]]) -> bool:
    """True for the last event of a build (seen by the dialog)."""
    return bool(event) and event.get("event") in ("installed", "cancelled", "error")
