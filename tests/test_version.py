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
