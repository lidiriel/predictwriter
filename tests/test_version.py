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
