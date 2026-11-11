#!/usr/bin/env python3
"""Zip ``extension/`` into ``dist/predictive-writer-<version>.oxt``.

The version (major.minor, e.g. ``1.0``) is read from
``extension/description.xml`` and must match ``predictive.__version__``.
"""

import argparse
import os
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"__pycache__", ".pytest_cache"}
SKIP_SUFFIXES = {".pyc", ".pyo"}
VERSION_FORMAT = re.compile(r"^\d+\.\d+$")


def description_version(src: Path) -> str:
    """Version declared in ``description.xml`` (what LibreOffice displays)."""
    text = (src / "description.xml").read_text(encoding="utf-8")
    match = re.search(r'<version\s+value="([^"]+)"', text)
    if not match:
        raise ValueError("no <version value=...> in description.xml")
    return match.group(1)


def package_version(src: Path) -> str:
    """``__version__`` of the ``predictive`` package."""
    text = (src / "python" / "pythonpath" / "predictive" / "__init__.py").read_text(
        encoding="utf-8"
    )
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise ValueError("no __version__ in predictive/__init__.py")
    return match.group(1)


def check_version(src: Path) -> str:
    """Return the version, or raise if the two declarations disagree."""
    declared, package = description_version(src), package_version(src)
    if declared != package:
        raise ValueError(
            "version mismatch: description.xml says %s, predictive/__init__.py %s"
            % (declared, package)
        )
    if not VERSION_FORMAT.match(declared):
        raise ValueError("version %r is not major.minor (e.g. 1.0)" % declared)
    return declared


def build(src: Path, out: Path) -> int:
    """Write the .oxt archive and return the number of files added."""
    out.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(src):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for name in sorted(filenames):
                path = Path(dirpath) / name
                if path.suffix in SKIP_SUFFIXES:
                    continue
                zf.write(path, path.relative_to(src).as_posix())
                count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--src", type=Path, default=ROOT / "extension")
    parser.add_argument(
        "--out", type=Path, help="default: dist/predictive-writer-<version>.oxt"
    )
    args = parser.parse_args()
    try:
        version = check_version(args.src)
    except ValueError as exc:
        sys.exit("error: %s" % exc)
    out = args.out or ROOT / "dist" / ("predictive-writer-%s.oxt" % version)
    count = build(args.src, out)
    print("wrote %s (version %s, %d files)" % (out, version, count))


if __name__ == "__main__":
    main()
