"""The version is declared twice; both must agree and be major.minor."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import build_oxt  # noqa: E402

import predictive  # noqa: E402


def test_versions_agree_and_are_major_minor():
    version = build_oxt.check_version(ROOT / "extension")
    assert version == predictive.__version__
    assert build_oxt.VERSION_FORMAT.match(version)


def test_mismatch_is_rejected(tmp_path):
    import pytest

    (tmp_path / "python" / "pythonpath" / "predictive").mkdir(parents=True)
    (tmp_path / "description.xml").write_text(
        '<version value="1.1"/>', encoding="utf-8"
    )
    (tmp_path / "python" / "pythonpath" / "predictive" / "__init__.py").write_text(
        '__version__ = "1.0"\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="mismatch"):
        build_oxt.check_version(tmp_path)


def test_describe():
    from predictive import buildinfo

    info = {
        "version": "1.0",
        "commit": "04ab2b9",
        "commit_date": "2026-11-11T20:35:10+01:00",
    }
    assert (
        buildinfo.describe(info)
        == "Predictive Writer 1.0 — commit 04ab2b9 of 2026-11-11 20:35"
    )
    info["modified"] = True
    assert buildinfo.describe(info).endswith("(with uncommitted changes)")
    assert buildinfo.describe({"version": "1.0", "built": "2026-11-12T08:00:00"}) == (
        "Predictive Writer 1.0 — built 2026-11-12 08:00"
    )
    assert "development version" in buildinfo.describe({})


def test_buildinfo_is_written_into_the_archive(tmp_path):
    import json
    import zipfile

    src = tmp_path / "ext"
    (src / "python" / "pythonpath" / "predictive").mkdir(parents=True)
    (src / "description.xml").write_text('<version value="2.3"/>', encoding="utf-8")
    out = tmp_path / "x.oxt"
    info = build_oxt.build_info("2.3", ROOT)
    build_oxt.build(src, out, info)
    with zipfile.ZipFile(out) as z:
        stored = json.loads(z.read(build_oxt.BUILDINFO_PATH))
    assert stored["version"] == "2.3" and stored["built"]
    # This project is a git repository: the last commit is recorded.
    assert len(stored["commit"]) >= 7 and stored["commit_date"][:4].isdigit()
    assert not (src / build_oxt.BUILDINFO_PATH).exists()  # source tree untouched


def test_git_info_outside_a_repository(tmp_path):
    assert build_oxt.git_info(tmp_path) == {}


def test_license_is_in_the_archive(tmp_path):
    import zipfile

    src = tmp_path / "ext"
    src.mkdir()
    (src / "description.xml").write_text('<version value="1.0"/>', encoding="utf-8")
    out = tmp_path / "x.oxt"
    build_oxt.build(src, out)
    with zipfile.ZipFile(out) as z:
        text = z.read("LICENSE").decode("utf-8")
    assert "Mozilla Public License Version 2.0" in text and "Ludovic Mercier" in text


def _git_repo(path):
    import subprocess

    import os

    env = dict(
        os.environ,  # keeps PATH (git.exe on Windows)
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@example.invalid",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@example.invalid",
        GIT_CONFIG_GLOBAL=os.devnull,  # ignore the user's git config
        GIT_CONFIG_NOSYSTEM="1",
    )

    def git(*args):
        subprocess.run(
            ["git", "-C", str(path), *args], check=True, env=env, capture_output=True
        )

    git("init", "-q")
    return git


def test_version_comes_from_the_last_tag(tmp_path):
    git = _git_repo(tmp_path)
    (tmp_path / "f").write_text("1")
    git("add", "f")
    git("commit", "-q", "-m", "one")
    assert build_oxt.tag_version(tmp_path) is None  # no tag yet
    git("tag", "v2.3")
    assert build_oxt.tag_version(tmp_path) == ("2.3", "v2.3", 0)
    (tmp_path / "f").write_text("2")
    git("commit", "-q", "-am", "two")
    assert build_oxt.tag_version(tmp_path) == ("2.3", "v2.3", 1)
    git("tag", "release-candidate")  # not a v* tag: ignored
    assert build_oxt.tag_version(tmp_path) == ("2.3", "v2.3", 1)


def test_bad_version_tag_is_rejected(tmp_path):
    import pytest

    git = _git_repo(tmp_path)
    (tmp_path / "f").write_text("1")
    git("add", "f")
    git("commit", "-q", "-m", "one")
    git("tag", "v2")
    with pytest.raises(ValueError, match="v<major>.<minor>"):
        build_oxt.tag_version(tmp_path)


def test_fallback_without_git(tmp_path):
    version, origin = build_oxt.resolve_version(ROOT / "extension", tmp_path)
    assert version == predictive.__version__ and origin == {"tag": None}


def test_archive_description_gets_the_tag_version(tmp_path):
    import zipfile

    src = tmp_path / "ext"
    src.mkdir()
    (src / "description.xml").write_text('<version value="1.1"/>', encoding="utf-8")
    out = tmp_path / "x.oxt"
    build_oxt.build(src, out, {"version": "7.4"})
    with zipfile.ZipFile(out) as z:
        assert '<version value="7.4"/>' in z.read("description.xml").decode()
    assert '"1.1"' in (src / "description.xml").read_text()  # source untouched


def test_describe_commits_since_tag():
    from predictive import buildinfo

    info = {
        "version": "1.1",
        "commits_since_tag": 3,
        "commit": "abc1234",
        "commit_date": "2026-11-20T09:00:00+01:00",
    }
    assert buildinfo.describe(info) == (
        "Predictive Writer 1.1 (+3 commits) — commit abc1234 of 2026-11-20 09:00"
    )
    info["commits_since_tag"] = 0
    assert "(+" not in buildinfo.describe(info)
