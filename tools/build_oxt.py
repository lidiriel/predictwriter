#!/usr/bin/env python3
"""Zip ``extension/`` into ``dist/predictive-writer-<version>.oxt``.

The version (major.minor, e.g. ``1.0``) is read from
``extension/description.xml`` and must match ``predictive.__version__``.
A ``buildinfo.json`` is added inside the archive (not in the source tree)
with the version and the last git commit (hash, date, uncommitted changes),
shown at the bottom of the Options page.
"""

import argparse
import datetime
import json
import os
import re
import subprocess
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


BUILDINFO_PATH = "python/pythonpath/predictive/buildinfo.json"


def git_info(root: Path) -> dict:
    """Last commit (short hash, ISO date) and whether tracked files changed.

    Returns ``{}`` if ``root`` is not a git repository or git is missing.
    """

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    try:
        commit, date = git("log", "-1", "--format=%h%n%cI").splitlines()
        changes = git("status", "--porcelain", "--untracked-files=no")
    except (OSError, subprocess.CalledProcessError, ValueError):
        return {}
    return {"commit": commit, "commit_date": date, "modified": bool(changes)}


def build_info(version: str, root: Path = ROOT) -> dict:
    info = {
        "version": version,
        "built": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    info.update(git_info(root))
    return info


def build(src: Path, out: Path, info: dict = None) -> int:
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
        if info is not None:
            zf.writestr(BUILDINFO_PATH, json.dumps(info, indent=2) + "\n")
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
    info = build_info(version)
    count = build(args.src, out, info)
    if "commit" in info:
        state = " + uncommitted changes" if info["modified"] else ""
        origin = "commit %s of %s%s" % (info["commit"], info["commit_date"], state)
    else:
        origin = "no git information"
    print("wrote %s (version %s, %s, %d files)" % (out, version, origin, count))


if __name__ == "__main__":
    main()
