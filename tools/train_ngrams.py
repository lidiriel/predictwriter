#!/usr/bin/env python3
"""Build an n-gram model (pressagio SQLite format) for context prediction.

Command-line front end of ``predictive.model_builder`` (the code the Options
page runs); see that module for the details.

Sources (combinable, read in this order until ``--max-words``):

* ``--folder DIR``: your own ``.odt``, ``.txt`` and ``.pdf`` files
  (recursively; PDF needs ``pdftotext`` from poppler-utils).
* ``--wikisource``, ``--wikipedia``, ``--wikimedia NAME``: Wikimedia projects
  in the model's language, streamed from the "CirrusSearch" dumps in slices
  (HTTP range requests), never stored. ``--max-download-mb`` is the total
  download budget for all of them.

Licence: Wikimedia text is CC BY-SA 4.0 (and GFDL). A model built from it is
for your own use; the ``_meta`` table records the source, dump date and
licence so the attribution travels with the file if you ever share it.

Examples::

    python3 tools/train_ngrams.py --wikisource                 # fr_FR, 250 MB
    python3 tools/train_ngrams.py --wikipedia --max-download-mb 100
    python3 tools/train_ngrams.py --folder ~/Documents --wikisource
"""

import argparse
import sys
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "extension" / "python" / "pythonpath"))

from predictive import model_builder as mb  # noqa: E402

# Re-exported for compatibility (tests, scripts).
from predictive.model_builder import (  # noqa: E402,F401
    MB,
    NgramStore,
    RangeReader,
    iter_cirrus_texts,
    iter_folder_texts,
    iter_odt_text,
    latest_dump_date,
    tokenize,
    train,
    wikipedia_part_url,
)

__all__ = [
    "MB",
    "NgramStore",
    "RangeReader",
    "iter_cirrus_texts",
    "iter_folder_texts",
    "iter_odt_text",
    "latest_dump_date",
    "main",
    "tokenize",
    "train",
    "wikipedia_part_url",
]

DEFAULT_OUT_DIR = Path.home() / ".config/libreoffice/4/user/predictive-writer"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lang", default="fr_FR", help="model language (fr_FR)")
    parser.add_argument("--folder", action="append", type=Path, default=[])
    parser.add_argument("--wikisource", action="store_true", help="use Wikisource")
    parser.add_argument("--wikipedia", action="store_true", help="use Wikipedia")
    parser.add_argument(
        "--wikimedia",
        action="append",
        default=[],
        choices=sorted(mb.WIKIMEDIA_PROJECTS),
        help="another Wikimedia project (repeatable)",
    )
    parser.add_argument(
        "--max-download-mb",
        type=int,
        help="total download for all Wikimedia sources (MB, default 250)",
    )
    parser.add_argument(
        "--slice-mb", type=int, default=25, help="download slice size (MB, 25)"
    )
    parser.add_argument(
        "--max-slices",
        type=int,
        help="old option: download budget = max-slices x slice-mb",
    )
    parser.add_argument(
        "--pause", type=float, default=2.0, help="seconds between slices (default 2)"
    )
    parser.add_argument("--dump-date", "--wikipedia-date", help="dump date YYYYMMDD")
    parser.add_argument("--max-words", type=int, default=30_000_000)
    parser.add_argument("--min-count", type=int, default=2)
    parser.add_argument("--order", type=int, default=3, help="n-gram size (3)")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    projects = list(args.wikimedia)
    for flag, project in (
        (args.wikisource, "wikisource"),
        (args.wikipedia, "wikipedia"),
    ):
        if flag and project not in projects:
            projects.append(project)
    if not projects and not args.folder:
        parser.error("give --folder and/or --wikisource / --wikipedia / --wikimedia")

    if args.max_download_mb is not None:
        budget_mb = args.max_download_mb
    elif args.max_slices is not None:
        budget_mb = args.max_slices * args.slice_mb
    else:
        budget_mb = 250
    limits = mb.BuildLimits(
        max_download_bytes=budget_mb * MB,
        max_words=args.max_words,
        min_count=args.min_count,
        order=args.order,
        slice_bytes=args.slice_mb * MB,
        pause=args.pause,
    )
    # Your documents first: they are usually smaller and more relevant.
    sources: list = [mb.FolderSource(str(f)) for f in args.folder]
    sources += [mb.WikimediaSource(p, args.lang, args.dump_date) for p in projects]
    out = args.out or DEFAULT_OUT_DIR / ("ngrams_%s.sqlite" % args.lang)

    def show(event: dict) -> None:
        print(mb._human(event), file=sys.stderr, flush=True)

    try:
        mb.build_model(sources, str(out), args.lang, limits, progress=show)
    except mb.Cancelled:
        print("cancelled; no model written", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted; no model written", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
