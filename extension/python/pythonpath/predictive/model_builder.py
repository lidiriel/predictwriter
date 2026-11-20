"""Build the n-gram model used for context prediction (plain Python, no ``uno``).

The model is a SQLite file in pressagio's format (tables ``_1_gram`` ...
``_3_gram`` plus a ``_meta`` table with the sources and the licence).

Sources (read in the order given, until ``max_words``):

* :class:`FolderSource`: your ``.odt`` and ``.txt`` files, recursively.
* :class:`WikimediaSource`: a Wikimedia project (Wikisource, Wikipedia,
  Wikinews, Wikiquote) in one language, from the "CirrusSearch" dumps (plain
  article text, one JSON document per line, bz2). The dump files are read in
  order, in slices fetched with HTTP range requests, until the **download
  budget** shared by all sources (``max_download_bytes``) is used up; the
  files are never stored.

Text is tokenised exactly as pressagio tokenises the context at prediction
time. Counts are accumulated in batches into SQLite staging tables (bounded
memory); at the end SQLite sums them, prunes rare n-grams (``min_count``) and
fills pressagio's tables, then the file is renamed into place.

:func:`build_model` reports progress through a callback (dict events) and
can be cancelled. ``python -m predictive.model_builder --json ...`` runs it
as a separate process (as the Options page does) and prints one JSON event
per line on standard output.

Licence: Wikimedia text is CC BY-SA 4.0 (and GFDL); the ``_meta`` table
records the sources, dump date and licence so the attribution travels with
the model.
"""

import argparse
import bz2
import datetime
import json
import os
import re
import signal
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from pathlib import Path
from typing import (
    BinaryIO,
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    NamedTuple,
    Optional,
    Sequence,
    Tuple,
)

import pressagio.character
import pressagio.tokenizer

from predictive.nextword import normalize

CIRRUS_BASE = "https://dumps.wikimedia.org/other/cirrus_search_index/"
USER_AGENT = "predictive-writer-model-builder/1.3 (local n-gram training)"
MB = 1024 * 1024

# Wikimedia projects: name -> (suffix of the dump name, display name).
WIKIMEDIA_PROJECTS: Dict[str, Tuple[str, str]] = {
    "wikisource": ("wikisource", "Wikisource"),
    "wikipedia": ("wiki", "Wikipedia"),
    "wikinews": ("wikinews", "Wikinews"),
    "wikiquote": ("wikiquote", "Wikiquote"),
}
WIKIMEDIA_LICENCE = (
    "%s text, CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0/); "
    "authors: see the history of each page; changes: tokenised and counted "
    "as n-grams"
)

# Rough figures measured on a French Wikipedia build (30 M words from 236 MB
# of compressed dump, about 100,000 words/s, model about 10.5 bytes/word);
# used only for the estimate shown before a build.
WORDS_PER_DOWNLOADED_MB = 127_000
WORDS_PER_SECOND = 100_000
MODEL_BYTES_PER_WORD = 10.5
TEMP_BYTES_PER_WORD = 40.0  # staging tables before pruning, peak

Progress = Callable[[dict], None]
CancelCheck = Callable[[], bool]


class Cancelled(Exception):
    """The build was cancelled (no model written)."""


# Tokenisation -------------------------------------------------------------------
# A token is a run of characters that pressagio treats neither as blank
# space nor as separator (see pressagio.tokenizer.ForwardTokenizer).
_NOT_TOKEN = re.escape(pressagio.character.blankspaces + pressagio.character.separators)
_TOKEN_RE = re.compile("[^%s\\s]+" % _NOT_TOKEN)


def tokenize(text: str) -> List[str]:
    """Lowercase tokens, as pressagio sees them at prediction time."""
    text = pressagio.tokenizer.preprocess(normalize(text))
    return _TOKEN_RE.findall(text.lower())


# Downloading ----------------------------------------------------------------------
class DownloadBudget:
    """Bytes still allowed to be downloaded, shared by all the sources."""

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max(0, int(max_bytes))
        self.used = 0

    @property
    def remaining(self) -> int:
        return max(0, self.max_bytes - self.used)


def _http_get_text(url: str, timeout: int = 60) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def _http_range(url: str, start: int, end: int) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Range": "bytes=%d-%d" % (start, end)}
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            if response.status != 206:
                raise RuntimeError("server ignored the range request")
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 416:  # range starts after the end of the file
            return b""
        raise


class RangeReader:
    """File-like reader fetching a URL in slices with HTTP range requests.

    Limits the load on the server: at most ``max_slices`` requests of
    ``slice_size`` bytes (and no more than the shared ``budget`` allows),
    with ``pause`` seconds between them. Reading past the limit returns
    ``b""`` (end of stream): ``iter_cirrus_texts`` then stops after the last
    complete article.
    """

    def __init__(
        self,
        url: str,
        slice_size: int = 25 * MB,
        max_slices: Optional[int] = 10,
        pause: float = 2.0,
        fetch: Optional[Callable[[str, int, int], bytes]] = None,
        sleep: Callable[[float], None] = time.sleep,
        budget: Optional[DownloadBudget] = None,
        on_slice: Optional[Callable[["RangeReader", int], None]] = None,
        cancel: Optional[CancelCheck] = None,
    ) -> None:
        self.url = url
        self.slice_size = slice_size
        self.max_slices = max_slices
        self.pause = pause
        self._fetch = fetch or _http_range
        self._sleep = sleep
        self.budget = budget
        self._on_slice = on_slice
        self._cancel = cancel
        self._buffer = b""
        self._offset = 0  # next byte to request
        self.slices = 0
        self.downloaded = 0
        self.end_of_file = False  # the whole file has been read
        self._eof = False

    def read(self, size: int = -1) -> bytes:
        while not self._eof and (size < 0 or len(self._buffer) < size):
            if self.max_slices is not None and self.slices >= self.max_slices:
                self._eof = True
                break
            length = self.slice_size
            if self.budget is not None:
                length = min(length, self.budget.remaining)
                if length <= 0:
                    self._eof = True
                    break
            if self._cancel is not None and self._cancel():
                raise Cancelled()
            if self.slices and self.pause > 0:
                self._sleep(self.pause)
            start = self._offset
            data = self._fetch(self.url, start, start + length - 1)
            self.slices += 1
            self.downloaded += len(data)
            self._offset += len(data)
            if self.budget is not None:
                self.budget.used += len(data)
            if len(data) < length:
                self._eof = self.end_of_file = True
            self._buffer += data
            if self._on_slice is not None:
                self._on_slice(self, len(data))
        if size < 0:
            size = len(self._buffer)
        chunk, self._buffer = self._buffer[:size], self._buffer[size:]
        return chunk


def iter_cirrus_texts(stream: BinaryIO, chunk_size: int = 1 << 20) -> Iterator[str]:
    """Article texts from a CirrusSearch dump file (bz2, possibly multi-stream).

    Lines alternate ``{"index": ...}`` and the document; only articles
    (namespace 0) with a ``text`` field are yielded.
    """
    decompressor = bz2.BZ2Decompressor()
    pending = b""
    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            break
        while chunk:
            data = decompressor.decompress(chunk)
            pending += data
            if decompressor.eof:  # next bz2 stream in the same file
                chunk = decompressor.unused_data
                decompressor = bz2.BZ2Decompressor()
            else:
                chunk = b""
        lines = pending.split(b"\n")
        pending = lines.pop()
        for line in lines:
            text = _cirrus_text(line)
            if text:
                yield text
    text = _cirrus_text(pending)
    if text:
        yield text


def _cirrus_text(line: bytes) -> Optional[str]:
    if not line.strip():
        return None
    try:
        doc = json.loads(line)
    except ValueError:
        return None
    if "text" not in doc or doc.get("namespace", 0) != 0:
        return None
    return doc["text"]


def latest_dump_date(fetch_text: Callable[[str], str] = _http_get_text) -> str:
    """Most recent dated folder of the CirrusSearch dumps (YYYYMMDD)."""
    dates = re.findall(r'href="(\d{8})/"', fetch_text(CIRRUS_BASE))
    if not dates:
        raise RuntimeError("no dump found at " + CIRRUS_BASE)
    return max(dates)


def dump_folder_url(wiki: str, date: str) -> str:
    """Folder of a wiki's article dump, e.g. wiki='frwikisource'."""
    return "%s%s/index_name=%s_content/" % (CIRRUS_BASE, date, wiki)


def wikipedia_part_url(wiki: str, date: str, part: int) -> str:
    """URL of one file of a wiki's article dump, e.g. wiki='frwiki'."""
    return "%s%s_content-%s-%05d.json.bz2" % (
        dump_folder_url(wiki, date),
        wiki,
        date,
        part,
    )


def list_dump_files(
    wiki: str, date: str, fetch_text: Callable[[str], str] = _http_get_text
) -> List[Tuple[str, int]]:
    """``(url, size in bytes)`` of the dump files of ``wiki``, in order."""
    folder = dump_folder_url(wiki, date)
    listing = fetch_text(folder)
    files = []
    for match in re.finditer(
        r'href="([^"/]+\.json\.bz2)">[^<]*</a>\s+\S+\s+\S+\s+(\d+)', listing
    ):
        files.append((folder + match.group(1), int(match.group(2))))
    files.sort()
    return files


# Documents ------------------------------------------------------------------------
def iter_odt_text(path: Path) -> Iterator[str]:
    """Paragraphs and headings of an .odt file (standard library only)."""
    ns_text = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
    with zipfile.ZipFile(path) as odt:
        root = ET.fromstring(odt.read("content.xml"))
    for element in root.iter():
        if element.tag in ("{%s}p" % ns_text, "{%s}h" % ns_text):
            text = _odt_element_text(element, ns_text)
            if text.strip():
                yield text


def _odt_element_text(element: ET.Element, ns: str) -> str:
    parts = [element.text or ""]
    for child in element:
        tag = child.tag
        if tag == "{%s}s" % ns:
            parts.append(" " * int(child.get("{%s}c" % ns, "1")))
        elif tag in ("{%s}tab" % ns, "{%s}line-break" % ns):
            parts.append(" ")
        elif tag not in ("{%s}note" % ns,):  # skip footnote bodies
            parts.append(_odt_element_text(child, ns))
        parts.append(child.tail or "")
    return "".join(parts)


def iter_folder_texts(
    folder: Path, on_skip: Optional[Callable[[Path, Exception], None]] = None
) -> Iterator[str]:
    """Paragraphs of every .odt and .txt file under ``folder``."""
    for path in sorted(folder.rglob("*")):
        suffix = path.suffix.lower()
        try:
            if suffix == ".odt":
                yield from iter_odt_text(path)
            elif suffix == ".txt":
                with path.open(encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if line.strip():
                            yield line
        except (OSError, zipfile.BadZipFile, ET.ParseError) as exc:
            if on_skip is not None:
                on_skip(path, exc)


# Sources ---------------------------------------------------------------------------
class BuildLimits(NamedTuple):
    """How much to read and what to keep."""

    max_download_bytes: int = 250 * MB  # shared by all Wikimedia sources
    max_words: int = 30_000_000
    min_count: int = 2  # n-grams seen fewer times are dropped
    order: int = 3  # n-gram size
    slice_bytes: int = 25 * MB
    pause: float = 2.0  # seconds between two downloads


class _Context:
    """What a source needs while it is read."""

    def __init__(
        self,
        limits: BuildLimits,
        budget: DownloadBudget,
        progress: Progress,
        cancel: CancelCheck,
        meta: Dict[str, str],
        fetch_text: Callable[[str], str],
        fetch_range: Optional[Callable[[str, int, int], bytes]],
        sleep: Callable[[float], None],
    ) -> None:
        self.limits = limits
        self.budget = budget
        self.progress = progress
        self.cancel = cancel
        self.meta = meta
        self.fetch_text = fetch_text
        self.fetch_range = fetch_range
        self.sleep = sleep


class FolderSource:
    """Your documents: every .odt and .txt file under a folder."""

    kind = "folder"

    def __init__(self, folder: str) -> None:
        self.folder = Path(folder).expanduser()

    def describe(self) -> str:
        return "folder %s" % self.folder

    def texts(self, ctx: _Context) -> Iterator[str]:
        def skipped(path: Path, exc: Exception) -> None:
            ctx.progress({"event": "skipped", "path": str(path), "reason": str(exc)})

        if not self.folder.is_dir():
            raise FileNotFoundError("folder not found: %s" % self.folder)
        yield from iter_folder_texts(self.folder, skipped)


class WikimediaSource:
    """A Wikimedia project in one language (e.g. French Wikisource)."""

    kind = "wikimedia"

    def __init__(self, project: str, language: str, date: Optional[str] = None) -> None:
        if project not in WIKIMEDIA_PROJECTS:
            raise ValueError("unknown Wikimedia project: %s" % project)
        self.project = project
        self.language = language.split("_")[0].lower()  # fr_FR -> fr
        self.date = date

    @property
    def wiki(self) -> str:
        return self.language + WIKIMEDIA_PROJECTS[self.project][0]

    @property
    def display_name(self) -> str:
        return "%s (%s)" % (WIKIMEDIA_PROJECTS[self.project][1], self.language)

    def describe(self) -> str:
        return "%s, dump %s" % (self.display_name, self.date or "latest")

    def texts(self, ctx: _Context) -> Iterator[str]:
        date = self.date or latest_dump_date(ctx.fetch_text)
        self.date = date
        files = list_dump_files(self.wiki, date, ctx.fetch_text)
        if not files:
            raise RuntimeError("no dump files for %s on %s" % (self.wiki, date))
        licences = [ctx.meta["licence"]] if ctx.meta.get("licence") else []
        licences.append(WIKIMEDIA_LICENCE % self.display_name)
        ctx.meta["licence"] = " | ".join(licences)
        ctx.meta["%s_dump" % self.wiki] = date
        for url, size in files:
            if ctx.budget.remaining <= 0:
                return
            ctx.progress({"event": "file", "url": url, "size": size})

            def on_slice(reader: RangeReader, length: int) -> None:
                ctx.progress(
                    {
                        "event": "download",
                        "source": self.display_name,
                        "downloaded": ctx.budget.used,
                        "budget": ctx.budget.max_bytes,
                    }
                )

            reader = RangeReader(
                url,
                slice_size=ctx.limits.slice_bytes,
                max_slices=None,
                pause=ctx.limits.pause,
                fetch=ctx.fetch_range,
                sleep=ctx.sleep,
                budget=ctx.budget,
                on_slice=on_slice,
                cancel=ctx.cancel,
            )
            yield from iter_cirrus_texts(reader)
            if not reader.end_of_file:
                return  # stopped by the budget in the middle of this file


Source = object  # FolderSource or WikimediaSource (duck-typed: describe, texts)


# Counting --------------------------------------------------------------------------
def _columns(n: int) -> List[str]:
    """pressagio's column order for ``_n_gram``: word_2, word_1, word."""
    return ["word_%d" % i for i in range(n - 1, 0, -1)] + ["word"]


class NgramStore:
    """Counts n-grams (n = 1..order) into a SQLite file in pressagio's format.

    Batches of counts are appended to unindexed staging tables (fast); at the
    end SQLite sums them per n-gram, prunes rare ones and fills pressagio's
    tables in a single sorted pass (much faster than upserting into indexed
    tables batch after batch).
    """

    def __init__(self, path: str, order: int = 3, batch_entries: int = 3_000_000):
        self.path = path
        self.order = order
        self.batch_entries = batch_entries
        self.con = sqlite3.connect(path)
        self.con.execute("PRAGMA journal_mode = OFF")
        self.con.execute("PRAGMA synchronous = OFF")
        self.con.execute("PRAGMA temp_store = FILE")
        for n in range(1, order + 1):
            cols = _columns(n)
            self.con.execute(
                "CREATE TABLE _stage_%d (%s, count INTEGER)"
                % (n, ", ".join(c + " TEXT" for c in cols))
            )
        self.counters = [Counter() for _ in range(order)]
        self.words = 0

    def add(self, tokens: Sequence[str]) -> None:
        """Count the n-grams of one text (they never span two texts)."""
        self.words += len(tokens)
        for n in range(1, self.order + 1):
            counter = self.counters[n - 1]
            if n == 1:
                counter.update((t,) for t in tokens)
            else:
                counter.update(zip(*(tokens[i:] for i in range(n))))
        if sum(len(c) for c in self.counters) >= self.batch_entries:
            self.flush()

    def flush(self) -> None:
        for n, counter in enumerate(self.counters, start=1):
            if not counter:
                continue
            self.con.executemany(
                "INSERT INTO _stage_%d VALUES (%s)" % (n, ", ".join("?" * (n + 1))),
                (ngram + (count,) for ngram, count in counter.items()),
            )
            counter.clear()
        self.con.commit()

    def close(self) -> None:
        try:
            self.con.close()
        except sqlite3.Error:
            pass

    def finish(self, min_count: int, meta: dict) -> dict:
        """Sum, prune, index, store metadata and compact. Returns row counts."""
        self.flush()
        rows = {}
        for n in range(1, self.order + 1):
            cols = ", ".join(_columns(n))
            # Same schema as pressagio's DatabaseConnector.create_ngram_table.
            self.con.execute(
                "CREATE TABLE _%d_gram (%s, count INTEGER, UNIQUE(%s))"
                % (n, ", ".join(c + " TEXT" for c in _columns(n)), cols)
            )
            self.con.execute(
                "INSERT INTO _%d_gram SELECT %s, SUM(count) FROM _stage_%d "
                "GROUP BY %s HAVING SUM(count) >= ? ORDER BY %s"
                % (n, cols, n, cols, cols),
                (max(1, min_count),),
            )
            self.con.execute("DROP TABLE _stage_%d" % n)
            self.con.commit()
            rows[n] = self.con.execute("SELECT COUNT(*) FROM _%d_gram" % n).fetchone()[
                0
            ]
            # Next-word queries: context columns, then most frequent first.
            context = _columns(n)[:-1]
            self.con.execute(
                "CREATE INDEX idx_%d_gram_count ON _%d_gram (%s)"
                % (n, n, ", ".join(context + ["count DESC"]))
            )
        self.con.execute("CREATE TABLE _meta (key TEXT PRIMARY KEY, value TEXT)")
        meta = dict(meta)
        meta.update({"words": str(self.words), "order": str(self.order)})
        meta.update({"ngrams_%d" % n: str(c) for n, c in rows.items()})
        self.con.executemany(
            "INSERT OR REPLACE INTO _meta (key, value) VALUES (?, ?)", meta.items()
        )
        self.con.commit()
        self.con.execute("ANALYZE")
        self.con.execute("VACUUM")
        self.con.close()
        return rows


def train(
    texts: Iterable[Tuple[str, str]],
    out: str,
    order: int = 3,
    max_words: int = 30_000_000,
    min_count: int = 2,
    meta: Optional[dict] = None,
    progress_every: int = 1_000_000,
    progress: Optional[Progress] = None,
    cancel: Optional[CancelCheck] = None,
) -> dict:
    """Build the model at ``out`` from ``(source, text)`` pairs.

    The model is written to ``out + ".part"`` and renamed when complete; on
    cancellation or error the partial file is removed.
    """
    progress = progress or (lambda event: None)
    part = out + ".part"
    if os.path.exists(part):
        os.remove(part)
    store = NgramStore(part, order)
    start = time.monotonic()
    next_report = progress_every
    sources = set()
    try:
        for source, text in texts:
            if cancel is not None and cancel():
                raise Cancelled()
            tokens = tokenize(text)
            if not tokens:
                continue
            sources.add(source)
            store.add(tokens[: max(0, max_words - store.words)])
            if store.words >= next_report:
                elapsed = max(time.monotonic() - start, 1e-6)
                progress(
                    {
                        "event": "words",
                        "words": store.words,
                        "max_words": max_words,
                        "words_per_second": int(store.words / elapsed),
                    }
                )
                next_report += progress_every
            if store.words >= max_words:
                break
        if cancel is not None and cancel():
            raise Cancelled()
        progress({"event": "finishing", "words": store.words, "min_count": min_count})
        info = dict(meta or {})
        info.setdefault("created", datetime.date.today().isoformat())
        info["min_count"] = str(min_count)
        rows = store.finish(min_count, info)
        os.replace(part, out)
    except BaseException:
        store.close()
        if os.path.exists(part):
            os.remove(part)
        raise
    return {"words": store.words, "rows": rows, "sources": sorted(sources)}


# Whole build -----------------------------------------------------------------------
def estimate(sources: Sequence[Source], limits: BuildLimits) -> dict:
    """Rough figures to show before a build (no network access)."""
    has_web = any(getattr(s, "kind", "") == "wikimedia" for s in sources)
    download = limits.max_download_bytes if has_web else 0
    words = limits.max_words
    if has_web and not any(getattr(s, "kind", "") == "folder" for s in sources):
        words = min(words, int(download / MB * WORDS_PER_DOWNLOADED_MB))
    seconds = words / WORDS_PER_SECOND
    if download:
        slices = max(1, download // max(1, limits.slice_bytes))
        seconds += slices * limits.pause + download / (5 * MB)  # at ~5 MB/s
    return {
        "download_mb": int(download / MB),
        "words": int(words),
        "minutes": max(1, int(round(seconds / 60.0))),
        "model_mb": int(words * MODEL_BYTES_PER_WORD / MB),
        "disk_mb": int(words * TEMP_BYTES_PER_WORD / MB),
    }


def build_model(
    sources: Sequence[Source],
    out: str,
    language: str,
    limits: BuildLimits = BuildLimits(),
    progress: Optional[Progress] = None,
    cancel: Optional[CancelCheck] = None,
    fetch_text: Callable[[str], str] = _http_get_text,
    fetch_range: Optional[Callable[[str, int, int], bytes]] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Build the model at ``out`` from ``sources``; returns a summary dict.

    Raises :class:`Cancelled` if ``cancel()`` becomes true (no model written,
    an existing model at ``out`` is kept).
    """
    progress = progress or (lambda event: None)
    cancel = cancel or (lambda: False)
    if not sources:
        raise ValueError("no source selected")
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    meta: Dict[str, str] = {"language": language}
    budget = DownloadBudget(limits.max_download_bytes)
    ctx = _Context(
        limits, budget, progress, cancel, meta, fetch_text, fetch_range, sleep
    )
    start = time.monotonic()
    progress(
        {
            "event": "start",
            "sources": [s.describe() for s in sources],
            "estimate": estimate(sources, limits),
        }
    )

    def texts() -> Iterator[Tuple[str, str]]:
        for source in sources:
            progress({"event": "source", "name": source.describe()})
            for text in source.texts(ctx):
                yield source.kind, text

    result = train(
        texts(),
        str(out_path),
        limits.order,
        limits.max_words,
        limits.min_count,
        meta,
        progress=progress,
        cancel=cancel,
    )
    # The sources' descriptions are final now (dump dates resolved).
    con = sqlite3.connect(str(out_path))
    con.execute(
        "INSERT OR REPLACE INTO _meta (key, value) VALUES ('sources', ?)",
        ("; ".join(s.describe() for s in sources),),
    )
    con.commit()
    con.close()
    result.update(
        {
            "downloaded": budget.used,
            "seconds": round(time.monotonic() - start, 1),
            "size": out_path.stat().st_size,
            "out": str(out_path),
        }
    )
    progress(
        dict(result, event="done", rows={str(k): v for k, v in result["rows"].items()})
    )
    return result


# Command line / subprocess --------------------------------------------------------
def parse_source(spec: str, language: str) -> Source:
    """``wikisource``, ``wikipedia``... or ``folder:/path/to/documents``."""
    if spec.startswith("folder:"):
        return FolderSource(spec[len("folder:") :])
    return WikimediaSource(spec, language)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m predictive.model_builder",
        description="Build the n-gram model for Predictive Writer.",
    )
    parser.add_argument("--out", required=True, help="model file to write")
    parser.add_argument("--lang", default="fr_FR", help="language, e.g. fr_FR")
    parser.add_argument(
        "--source",
        action="append",
        required=True,
        help="wikisource, wikipedia, wikinews, wikiquote or folder:<path>; "
        "repeatable, read in order",
    )
    parser.add_argument("--max-download-mb", type=int, default=250)
    parser.add_argument("--max-words", type=int, default=30_000_000)
    parser.add_argument("--min-count", type=int, default=2)
    parser.add_argument("--slice-mb", type=int, default=25)
    parser.add_argument("--pause", type=float, default=2.0)
    parser.add_argument("--dump-date", help="Wikimedia dump date YYYYMMDD (latest)")
    parser.add_argument(
        "--json", action="store_true", help="one JSON event per line on stdout"
    )
    args = parser.parse_args(argv)

    sources = [parse_source(s, args.lang) for s in args.source]
    for source in sources:
        if isinstance(source, WikimediaSource):
            source.date = args.dump_date
    limits = BuildLimits(
        max_download_bytes=args.max_download_mb * MB,
        max_words=args.max_words,
        min_count=args.min_count,
        slice_bytes=args.slice_mb * MB,
        pause=args.pause,
    )
    stop = {"requested": False}

    def on_signal(signum, frame) -> None:  # cancellation from the parent
        stop["requested"] = True

    signal.signal(signal.SIGTERM, on_signal)
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, on_signal)

    def emit(event: dict) -> None:
        if args.json:
            print(json.dumps(event, ensure_ascii=False), flush=True)
        else:
            print(_human(event), file=sys.stderr, flush=True)

    try:
        build_model(
            sources,
            args.out,
            args.lang,
            limits,
            progress=emit,
            cancel=lambda: stop["requested"],
        )
    except Cancelled:
        emit({"event": "cancelled"})
        return 2
    except Exception as exc:  # reported to the parent, which shows it
        emit({"event": "error", "message": "%s: %s" % (type(exc).__name__, exc)})
        return 1
    return 0


def _human(event: dict) -> str:
    kind = event.get("event")
    if kind == "start":
        e = event["estimate"]
        return (
            "sources: %s\nestimate: %d MB download, %d words, ~%d min, model ~%d MB"
            % (
                "; ".join(event["sources"]),
                e["download_mb"],
                e["words"],
                e["minutes"],
                e["model_mb"],
            )
        )
    if kind == "download":
        return "%s: %.0f / %.0f MB" % (
            event["source"],
            event["downloaded"] / MB,
            event["budget"] / MB,
        )
    if kind == "words":
        return "%11d words (%d words/s)" % (event["words"], event["words_per_second"])
    if kind == "finishing":
        return "pruning (min count %d), indexing, compacting..." % event["min_count"]
    if kind == "done":
        return "wrote %s (%.0f MB) from %d words in %.0f s" % (
            event["out"],
            event["size"] / 1e6,
            event["words"],
            event["seconds"],
        )
    return json.dumps(event, ensure_ascii=False)


if __name__ == "__main__":
    sys.exit(main())
