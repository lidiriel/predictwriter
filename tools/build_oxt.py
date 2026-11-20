#!/usr/bin/env python3
"""Zip ``extension/`` into ``dist/predictive-writer-<version>.oxt``.

The version (major.minor, e.g. ``1.1``) comes from the last git tag
``v<major>.<minor>`` reachable from the current commit (``git describe``):
to release a version, tag it (``git tag v1.2``). Inside the archive,
``description.xml`` gets that version (the source file is not modified).
Without git or without such a tag, the version declared in
``extension/description.xml`` (which must match ``predictive.__version__``)
is used as a fallback.

A ``buildinfo.json`` is added inside the archive (not in the source tree)
with the version, the tag, the number of commits since the tag and the last
git commit (hash, date, uncommitted changes), shown at the bottom of the
Options page.
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
from typing import Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"__pycache__", ".pytest_cache"}
SKIP_SUFFIXES = {".pyc", ".pyo"}
VERSION_FORMAT = re.compile(r"^\d+\.\d+$")
TAG_GLOB = "v[0-9]*"  # tags considered as versions
_DESCRIBE = re.compile(r"^(?P<tag>.+)-(?P<distance>\d+)-g[0-9a-f]+$")


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


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def tag_version(root: Path) -> Optional[Tuple[str, str, int]]:
    """``(version, tag, commits since the tag)`` from the last ``vX.Y`` tag.

    Returns None if ``root`` is not a git repository, git is missing or no
    ``v*`` tag is reachable. Raises ``ValueError`` if the tag is not
    ``v<major>.<minor>``.
    """
    try:
        described = _git(root, "describe", "--tags", "--long", "--match", TAG_GLOB)
    except (OSError, subprocess.CalledProcessError):
        return None
    match = _DESCRIBE.match(described)
    if not match:
        return None
    tag, distance = match.group("tag"), int(match.group("distance"))
    version = tag[1:]
    if not VERSION_FORMAT.match(version):
        raise ValueError("tag %s is not v<major>.<minor> (e.g. v1.2)" % tag)
    return version, tag, distance


def resolve_version(src: Path, root: Path = ROOT) -> Tuple[str, dict]:
    """The version to build and how it was obtained (for buildinfo.json)."""
    found = tag_version(root)
    if found is not None:
        version, tag, distance = found
        return version, {"tag": tag, "commits_since_tag": distance}
    return check_version(src), {"tag": None}


BUILDINFO_PATH = "python/pythonpath/predictive/buildinfo.json"
LICENSE_FILE = ROOT / "LICENSE"  # copied to the root of the archive


def git_info(root: Path) -> dict:
    """Last commit (short hash, ISO date) and whether tracked files changed.

    Returns ``{}`` if ``root`` is not a git repository or git is missing.
    """

    try:
        commit, date = _git(root, "log", "-1", "--format=%h%n%cI").splitlines()
        changes = _git(root, "status", "--porcelain", "--untracked-files=no")
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


def build(
    src: Path, out: Path, info: dict = None, license_file: Path = LICENSE_FILE
) -> int:
    """Write the .oxt archive and return the number of files added.

    With ``info``, its ``version`` replaces the one in ``description.xml``
    (in the archive only) and ``buildinfo.json`` is added.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(src):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for name in sorted(filenames):
                path = Path(dirpath) / name
                if path.suffix in SKIP_SUFFIXES:
                    continue
                name_in_zip = path.relative_to(src).as_posix()
                if name_in_zip == "description.xml" and info is not None:
                    text = re.sub(
                        r'(<version\s+value=")[^"]*(")',
                        r"\g<1>%s\2" % info["version"],
                        path.read_text(encoding="utf-8"),
                        count=1,
                    )
                    zf.writestr(name_in_zip, text)
                else:
                    zf.write(path, name_in_zip)
                count += 1
        if info is not None:
            zf.writestr(BUILDINFO_PATH, json.dumps(info, indent=2) + "\n")
            count += 1
        if license_file is not None and license_file.exists():
            zf.write(license_file, "LICENSE")
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
        version, origin_info = resolve_version(args.src)
    except ValueError as exc:
        sys.exit("error: %s" % exc)
    out = args.out or ROOT / "dist" / ("predictive-writer-%s.oxt" % version)
    info = build_info(version)
    info.update(origin_info)
    count = build(args.src, out, info)
    if info["tag"]:
        source = "tag %s" % info["tag"]
        if info["commits_since_tag"]:
            source += " + %d commits" % info["commits_since_tag"]
    else:
        source = "description.xml (no v<major>.<minor> tag)"
    print("version %s from %s" % (version, source))
    if "commit" in info:
        state = " + uncommitted changes" if info["modified"] else ""
        origin = "commit %s of %s%s" % (info["commit"], info["commit_date"], state)
    else:
        origin = "no git information"
    print("wrote %s (version %s, %s, %d files)" % (out, version, origin, count))


if __name__ == "__main__":
    main()
