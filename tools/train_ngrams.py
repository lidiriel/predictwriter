#!/usr/bin/env python3
"""Build an n-gram model (pressagio SQLite format) for context prediction.

Sources (combinable):

* ``--wikipedia``: streams one or more files of the Wikipedia "CirrusSearch"
  dump (plain article text, one JSON document per line, bz2) over HTTPS and
  stops after ``--max-words`` words, so the ~630 MB files are never stored.
* ``--folder DIR``: your own ``.odt`` and ``.txt`` files (recursively).

Text is tokenised exactly as pressagio tokenises the context at prediction
time (same character classes, ``pressagio.tokenizer.preprocess``, lowercase,
and ``predictive.nextword.normalize``). Counts are accumulated in batches into
SQLite (bounded memory), rare n-grams are then pruned (``--min-count``) and
indexes are added. The model is written to a temporary file and renamed when
complete.

Licence: Wikipedia text is CC BY-SA 4.0 (and GFDL). A model built from it is
for your own use; the ``_meta`` table records the source, dump date and
licence so the attribution travels with the file if you ever share it.

Examples::

    python3 tools/train_ngrams.py --wikipedia                  # fr_FR, 30 M words
    python3 tools/train_ngrams.py --wikipedia --max-words 5000000
    python3 tools/train_ngrams.py --folder ~/Documents --wikipedia
"""

import argparse
import bz2
import datetime
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from pathlib import Path
from typing import BinaryIO, Iterable, Iterator, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "extension" / "python" / "pythonpath"))
import pressagio.character  # noqa: E402
import pressagio.tokenizer  # noqa: E402
from predictive.nextword import normalize  # noqa: E402

CIRRUS_BASE = "https://dumps.wikimedia.org/other/cirrus_search_index/"
USER_AGENT = "predictive-writer-train-ngrams/0.1 (local n-gram training)"
WIKIPEDIA_LICENCE = (
    "Wikipedia text, CC BY-SA 4.0 "
    "(https://creativecommons.org/licenses/by-sa/4.0/); authors: see the "
    "history of each article; changes: tokenised and counted as n-grams"
)
DEFAULT_OUT_DIR = Path.home() / ".config/libreoffice/4/user/predictive-writer"

# A token is a run of characters that pressagio treats neither as blank
# space nor as separator (see pressagio.tokenizer.ForwardTokenizer).
_NOT_TOKEN = re.escape(pressagio.character.blankspaces + pressagio.character.separators)
_TOKEN_RE = re.compile("[^%s\\s]+" % _NOT_TOKEN)


# Tokenisation -----------------------------------------------------------------
def tokenize(text: str) -> List[str]:
    """Lowercase tokens, as pressagio sees them at prediction time."""
    text = pressagio.tokenizer.preprocess(normalize(text))
    return _TOKEN_RE.findall(text.lower())


# Sources -----------------------------------------------------------------------
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


def _http_get(url: str, timeout: int = 60):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(request, timeout=timeout)


class RangeReader:
    """File-like reader fetching a URL in slices with HTTP range requests.

    Limits the load on the server: at most ``max_slices`` requests of
    ``slice_size`` bytes, with ``pause`` seconds between them. Reading past
    the last allowed slice returns ``b""`` (end of stream), which is fine for
    ``iter_cirrus_texts``: it simply stops after the last complete article.
    """

    def __init__(
        self,
        url: str,
        slice_size: int = 25 * 1024 * 1024,
        max_slices: int = 10,
        pause: float = 2.0,
        fetch=None,
        sleep=time.sleep,
    ) -> None:
        self.url = url
        self.slice_size = slice_size
        self.max_slices = max_slices
        self.pause = pause
        self._fetch = fetch or self._http_range
        self._sleep = sleep
        self._buffer = b""
        self._offset = 0  # next byte to request
        self.slices = 0
        self.downloaded = 0
        self._eof = False

    def _http_range(self, url: str, start: int, end: int) -> bytes:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Range": "bytes=%d-%d" % (start, end)},
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

    def read(self, size: int = -1) -> bytes:
        while not self._eof and (size < 0 or len(self._buffer) < size):
            if self.slices >= self.max_slices:
                self._eof = True
                break
            if self.slices and self.pause > 0:
                self._sleep(self.pause)
            start = self._offset
            data = self._fetch(self.url, start, start + self.slice_size - 1)
            self.slices += 1
            self.downloaded += len(data)
            self._offset += len(data)
            print(
                "slice %d/%d: %.1f MB (total %.0f MB)"
                % (
                    self.slices,
                    self.max_slices,
                    len(data) / 1e6,
                    self.downloaded / 1e6,
                ),
                file=sys.stderr,
                flush=True,
            )
            if len(data) < self.slice_size:
                self._eof = True  # end of the file
            self._buffer += data
        if size < 0:
            size = len(self._buffer)
        chunk, self._buffer = self._buffer[:size], self._buffer[size:]
        return chunk


def latest_dump_date() -> str:
    """Most recent dated folder of the CirrusSearch dumps (YYYYMMDD)."""
    with _http_get(CIRRUS_BASE) as response:
        listing = response.read().decode("utf-8", "replace")
    dates = re.findall(r'href="(\d{8})/"', listing)
    if not dates:
        raise RuntimeError("no dump found at " + CIRRUS_BASE)
    return max(dates)


def wikipedia_part_url(wiki: str, date: str, part: int) -> str:
    """URL of one file of a wiki's article dump, e.g. wiki='frwiki'."""
    folder = "%s%s/index_name=%s_content/" % (CIRRUS_BASE, date, wiki)
    return "%s%s_content-%s-%05d.json.bz2" % (folder, wiki, date, part)


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


def iter_folder_texts(folder: Path) -> Iterator[str]:
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
            print("skipped %s: %s" % (path, exc), file=sys.stderr)


# Counting ----------------------------------------------------------------------
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
) -> dict:
    """Build the model at ``out`` from ``(source, text)`` pairs."""
    part = out + ".part"
    if os.path.exists(part):
        os.remove(part)
    store = NgramStore(part, order)
    start = time.monotonic()
    next_report = progress_every
    sources = set()
    for source, text in texts:
        tokens = tokenize(text)
        if not tokens:
            continue
        sources.add(source)
        store.add(tokens[: max(0, max_words - store.words)])
        if store.words >= next_report:
            rate = store.words / max(time.monotonic() - start, 1e-6)
            print(
                "%11d words  (%.0f words/s)" % (store.words, rate),
                file=sys.stderr,
                flush=True,
            )
            next_report += progress_every
        if store.words >= max_words:
            break
    print(
        "pruning (min count %d), indexing, compacting..." % min_count, file=sys.stderr
    )
    info = dict(meta or {})
    info.setdefault("created", datetime.date.today().isoformat())
    info["min_count"] = str(min_count)
    rows = store.finish(min_count, info)
    os.replace(part, out)
    return {"words": store.words, "rows": rows, "sources": sorted(sources)}


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lang", default="fr_FR", help="model language (fr_FR)")
    parser.add_argument("--wikipedia", action="store_true", help="use Wikipedia")
    parser.add_argument(
        "--wikipedia-parts",
        default="0",
        help="dump files to read, e.g. '0' or '0,1' (each holds ~1 GB of text)",
    )
    parser.add_argument("--wikipedia-date", help="dump date YYYYMMDD (default: latest)")
    parser.add_argument(
        "--slice-mb",
        type=int,
        default=25,
        help="download Wikipedia in slices of this size (MB, default 25)",
    )
    parser.add_argument(
        "--max-slices",
        type=int,
        default=10,
        help="at most this many slices per dump file (default 10, i.e. 250 MB)",
    )
    parser.add_argument(
        "--pause", type=float, default=2.0, help="seconds between slices (default 2)"
    )
    parser.add_argument("--folder", action="append", type=Path, default=[])
    parser.add_argument("--max-words", type=int, default=30_000_000)
    parser.add_argument("--min-count", type=int, default=2)
    parser.add_argument("--order", type=int, default=3, help="n-gram size (3)")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if not args.wikipedia and not args.folder:
        parser.error("give --wikipedia and/or --folder")

    out = args.out or DEFAULT_OUT_DIR / ("ngrams_%s.sqlite" % args.lang)
    out.parent.mkdir(parents=True, exist_ok=True)
    meta = {"language": args.lang}
    sources_desc = []

    def texts() -> Iterator[Tuple[str, str]]:
        # Your documents first: they are usually smaller and more relevant.
        for folder in args.folder:
            sources_desc.append("folder %s" % folder)
            for text in iter_folder_texts(folder.expanduser()):
                yield "folder", text
        if args.wikipedia:
            wiki = args.lang.split("_")[0] + "wiki"
            date = args.wikipedia_date or latest_dump_date()
            meta["wikipedia_dump"] = date
            meta["licence"] = WIKIPEDIA_LICENCE
            for part in (int(p) for p in args.wikipedia_parts.split(",")):
                url = wikipedia_part_url(wiki, date, part)
                sources_desc.append(url)
                print("reading %s" % url, file=sys.stderr, flush=True)
                reader = RangeReader(
                    url,
                    slice_size=args.slice_mb * 1024 * 1024,
                    max_slices=args.max_slices,
                    pause=args.pause,
                )
                for text in iter_cirrus_texts(reader):
                    yield "wikipedia", text

    start = time.monotonic()
    result = train(texts(), str(out), args.order, args.max_words, args.min_count, meta)
    # Record the sources now that they are known (train() closed the file).
    con = sqlite3.connect(str(out))
    con.execute(
        "INSERT OR REPLACE INTO _meta (key, value) VALUES ('sources', ?)",
        ("; ".join(sources_desc),),
    )
    con.commit()
    con.close()
    rows = ", ".join("%d-grams: %d" % item for item in sorted(result["rows"].items()))
    print(
        "wrote %s (%.0f MB) from %d words in %.0f s; %s"
        % (
            out,
            out.stat().st_size / 1e6,
            result["words"],
            time.monotonic() - start,
            rows,
        ),
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
