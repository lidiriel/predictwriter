"""Tests for predictive.settings."""

import json

from predictive.settings import DEFAULTS, Settings


def test_defaults_when_missing(tmp_path):
    s = Settings(str(tmp_path / "settings.json"))
    assert s.values == DEFAULTS


def test_round_trip_and_type_check(tmp_path):
    path = tmp_path / "sub" / "settings.json"
    s = Settings(str(path))
    s["min_prefix"] = 4
    s["language"] = "en_US"
    s.save()
    assert Settings(str(path))["min_prefix"] == 4
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(min_prefix=True, enabled=1, debug="yes", extra=42)
    path.write_text(json.dumps(data), encoding="utf-8")
    s = Settings(str(path))
    assert s["min_prefix"] == 3  # bool rejected for an int setting: default
    assert s["enabled"] is True  # int rejected for a bool setting
    assert s["debug"] is False
    assert s["extra"] == 42  # unknown keys are kept


def test_corrupt_file_uses_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    assert Settings(str(path)).values == DEFAULTS
