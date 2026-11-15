"""Compliance tests driven directly by the official toml-test vectors.

These run without the external ``toml-test`` binary by walking the vector
tree copied under ``tests/tomltest-data``:

* every ``valid/*.toml`` must parse and match the adjacent ``*.json``
  description (using the same JSON shape the official runner expects);
* every ``invalid/*.toml`` must raise ``TOMLDecodeError``.

The binary-based check remains the authoritative gate (run via
``scripts/tomltest.sh``); this module gives a fast, dependency-light
regression that works anywhere pytest does.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

import ztoml
from ztoml import TOMLDecodeError
from ztoml import to_tomltest_json

DATA = Path(__file__).parent / "tomltest-data"
VALID = sorted((DATA / "valid").rglob("*.toml")) if DATA.exists() else []
INVALID = sorted((DATA / "invalid").rglob("*.toml")) if DATA.exists() else []


def _read_toml(path: Path) -> str:
    # Read as bytes and decode without newline translation, so CRLF/bare-CR
    # vectors keep their exact control characters.  A decode failure is
    # surfaced as an error the parser path is expected to reject.
    data = path.read_bytes()
    return data.decode("utf-8", errors="strict")


def _parse_temporal(kind: str, value: str):
    import datetime as dt
    if kind == "datetime":
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if kind == "datetime-local":
        return dt.datetime.fromisoformat(value)
    if kind == "date-local":
        return dt.date.fromisoformat(value)
    if kind == "time-local":
        return dt.time.fromisoformat(value)
    raise ValueError(kind)


def _desc_equal(actual, expected) -> bool:
    """Compare two toml-test description trees by value, mirroring the Go
    runner, which compares numbers/dates numerically rather than as text."""
    if isinstance(actual, dict) and isinstance(expected, dict):
        # Typed leaf: {"type": ..., "value": ...}
        if "type" in actual and "value" in actual:
            if actual["type"] != expected["type"]:
                return False
            kind, av, ev = actual["type"], actual["value"], expected["value"]
            if kind == "float":
                fa, fe = float(av), float(ev)
                if math.isnan(fa) and math.isnan(fe):
                    return True
                if fa == fe:
                    # Preserve the signed-zero distinction, which TOML keeps.
                    if fa == 0.0 and fe == 0.0:
                        return math.copysign(1.0, fa) == math.copysign(1.0, fe)
                    return True
                return False
            if kind == "integer":
                return int(av) == int(ev)
            if kind == "bool":
                return av == ev
            if kind in ("datetime", "datetime-local",
                        "date-local", "time-local"):
                return _parse_temporal(kind, av) == _parse_temporal(kind, ev)
            return av == ev  # string
        # Nested table.
        if actual.keys() != expected.keys():
            return False
        return all(_desc_equal(actual[k], expected[k]) for k in actual)
    if isinstance(actual, list) and isinstance(expected, list):
        if len(actual) != len(expected):
            return False
        return all(_desc_equal(a, e) for a, e in zip(actual, expected))
    return actual == expected


@pytest.mark.skipif(not DATA.exists(), reason="toml-test vectors not copied")
@pytest.mark.parametrize("toml_path", VALID, ids=lambda p: str(p.relative_to(DATA)))
def test_valid_vectors(toml_path: Path):
    text = _read_toml(toml_path)
    parsed = ztoml.loads(text)
    expected = json.loads(toml_path.with_suffix(".json").read_text(encoding="utf-8"))
    actual = json.loads(to_tomltest_json(parsed))
    assert _desc_equal(actual, expected), (actual, expected)


@pytest.mark.skipif(not DATA.exists(), reason="toml-test vectors not copied")
@pytest.mark.parametrize("toml_path", INVALID, ids=lambda p: str(p.relative_to(DATA)))
def test_invalid_vectors(toml_path: Path):
    try:
        text = _read_toml(toml_path)
    except UnicodeDecodeError:
        # Invalid UTF-8 must be rejected before/at parse time; the bytes
        # never forming a str is an equally valid rejection.
        return
    with pytest.raises(TOMLDecodeError):
        ztoml.loads(text)
