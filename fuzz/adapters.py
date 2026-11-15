"""Adapters that give every parser-under-test one common interface.

Each adapter returns an :class:`Outcome`: either a *parsed* result (rendered
as a canonical, parser-independent token tree) or a *rejected* result.  A
fourth state, *crashed*, means the parser raised something other than its
documented parse error -- that is always a finding on its own.
"""

from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass

import rtoml
import tomllib
import tomlkit

import ztoml
from ztoml import TOMLDecodeError

PARSED = "parsed"
REJECTED = "rejected"
CRASHED = "crashed"


@dataclass(frozen=True)
class Outcome:
    kind: str
    # For PARSED: a canonical token tree of (type-tag, ...) tuples; for the
    # error states: the exception class name.
    payload: object
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.kind == PARSED


# ---------------------------------------------------------------- canonical form
def canonicalize(value):
    """Turn a parsed Python value into a comparable structural token tree.

    Floats are tagged so NaN compares equal to NaN and -0.0 is distinguished
    from +0.0.  Date/time values keep their exact textual identity, which is
    what differential parsing actually promises.
    """
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        return ("int", value)
    if isinstance(value, float):
        if math.isnan(value):
            return ("float", "nan")
        if math.isinf(value):
            return ("float", "inf" if value > 0 else "-inf")
        # Normalize -0.0 vs 0.0 explicitly.
        if value == 0.0:
            return ("float", "-0.0" if math.copysign(1.0, value) < 0 else "0.0")
        return ("float", repr(value))
    if isinstance(value, str):
        return ("str", value)
    if isinstance(value, _dt.datetime):
        return ("datetime", value.isoformat())
    if isinstance(value, _dt.date):
        return ("date", value.isoformat())
    if isinstance(value, _dt.time):
        return ("time", value.isoformat())
    if isinstance(value, (list, tuple)):
        return ("array", tuple(canonicalize(v) for v in value))
    if isinstance(value, dict):
        # Sort: key order in the output mirrors insertion, but differential
        # comparison cares about mapping identity, not presentation order.
        return ("table", tuple(sorted(
            (k, canonicalize(v)) for k, v in value.items()
        )))
    # tomlkit sometimes returns its own wrapper types; unwrap and retry.
    unwrap = getattr(value, "unwrap", None)
    if unwrap is not None:
        return canonicalize(unwrap())
    raise TypeError(f"cannot canonicalize {type(value)!r}")


# --------------------------------------------------------------------- adapters
def run_ztoml(text: str) -> Outcome:
    try:
        return Outcome(PARSED, canonicalize(ztoml.loads(text)))
    except TOMLDecodeError as exc:
        return Outcome(REJECTED, type(exc).__name__, str(exc))
    except Exception as exc:  # noqa: BLE001 -- any escape is a real crash
        return Outcome(CRASHED, type(exc).__name__, repr(exc))


def run_tomllib(text: str) -> Outcome:
    try:
        return Outcome(PARSED, canonicalize(tomllib.loads(text)))
    except tomllib.TOMLDecodeError as exc:
        return Outcome(REJECTED, type(exc).__name__, str(exc))
    except Exception as exc:  # noqa: BLE001
        return Outcome(CRASHED, type(exc).__name__, repr(exc))


def run_rtoml(text: str) -> Outcome:
    try:
        return Outcome(PARSED, canonicalize(rtoml.loads(text)))
    except rtoml.TomlParsingError as exc:
        return Outcome(REJECTED, type(exc).__name__, str(exc))
    except Exception as exc:  # noqa: BLE001
        return Outcome(CRASHED, type(exc).__name__, repr(exc))


def run_tomlkit(text: str) -> Outcome:
    try:
        doc = tomlkit.parse(text)
        # tomlkit returns a TOMLDocument wrapping plain values; unwrap first.
        return Outcome(PARSED, canonicalize(doc.value))
    except tomlkit.exceptions.TOMLKitError as exc:
        return Outcome(REJECTED, type(exc).__name__, str(exc))
    except Exception as exc:  # noqa: BLE001
        return Outcome(CRASHED, type(exc).__name__, repr(exc))


# Primary differential trio as the user requested: ours, stdlib, one third
# party.  tomlkit stays available as a tiebreaker / second opinion.
TRIO = {
    "ztoml": run_ztoml,
    "tomllib": run_tomllib,
    "rtoml": run_rtoml,
}
EXTRA = {
    "tomlkit": run_tomlkit,
}
ALL_PARSERS = {**TRIO, **EXTRA}
