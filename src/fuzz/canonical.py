"""Canonical normalization for cross-engine value comparison.

All four parsers ultimately represent the same TOML value model, but their
Python types differ. This module maps any engine's output to a structure of
immutable, hashable primitives so two parses can be compared with ``==`` and
stored in a set for dedup.

Normalization rules
-------------------
* dict      -> tuple of (key, canonical) sorted by key
* list      -> tuple of canonical children
* bool/int  -> ("int" | "bool", value)   (bool kept distinct from int,
  matching TOML's separate types even though Python conflates them)
* float     -> ("float", bit-pattern)    so NaN/-0.0 compare correctly
* str       -> ("string", value)
* the four date/time kinds -> tagged tuples with fractional seconds
  truncated to microseconds; a precision note is recorded when a parser
  carried more than six fractional digits (information the stdlib types
  cannot hold at all)
"""

from __future__ import annotations

import datetime as _dt
import math
import struct

from tomlone._datetimes import (
    LocalDate as TLocalDate,
    LocalDateTime as TLocalDateTime,
    LocalTime as TLocalTime,
    OffsetDateTime as TOffsetDateTime,
)

_MAX_MICRO_DIGITS = 6


def _float_tag(value: float) -> tuple:
    if math.isnan(value):
        payload = "nan"
    elif math.isinf(value):
        payload = "inf+" if value > 0 else "inf-"
    else:
        payload = struct.pack(">d", value).hex()
    return ("float", payload)


def _truncate_fraction(fraction: str | None, notes: list, path: str) -> int:
    """Turn a TOML fraction string ('.123...') into microseconds."""
    if fraction is None:
        return 0
    digits = fraction[1:]
    if len(digits) > _MAX_MICRO_DIGITS:
        notes.append((path, f"fractional precision {len(digits)} digits truncated to 6"))
        digits = digits[:_MAX_MICRO_DIGITS]
    return int(digits.ljust(_MAX_MICRO_DIGITS, "0"))


def canonicalize(value: object, path: str = "$", notes: list | None = None):
    if notes is None:
        notes = []
    canon = _canon(value, path, notes)
    return canon, notes


def _canon(value: object, path: str, notes: list):
    if isinstance(value, dict):
        items = sorted(
            (k, _canon(v, f"{path}.{k}", notes)) for k, v in value.items()
        )
        return ("table", tuple(items))
    if isinstance(value, list):
        return ("array", tuple(_canon(v, f"{path}[{i}]", notes)
                               for i, v in enumerate(value)))
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        return ("int", value)
    if isinstance(value, float):
        return _float_tag(value)
    if isinstance(value, str):
        return ("string", value)
    # stdlib datetime family (tomllib / rtoml / pytomlpp)
    if isinstance(value, _dt.datetime):
        if value.tzinfo is not None:
            off = value.utcoffset()
            minutes = int(off.total_seconds() // 60) if off is not None else 0
            return ("datetime", value.year, value.month, value.day,
                    value.hour, value.minute, value.second,
                    value.microsecond, minutes)
        return ("datetime-local", value.year, value.month, value.day,
                value.hour, value.minute, value.second, value.microsecond)
    if isinstance(value, _dt.date):
        return ("date-local", value.year, value.month, value.day)
    if isinstance(value, _dt.time):
        return ("time-local", value.hour, value.minute, value.second,
                value.microsecond)
    # tomlone's own date/time types
    if isinstance(value, TOffsetDateTime):
        d, t, o = value.date, value.time, value.offset
        return ("datetime", d.year, d.month, d.day, t.hour, t.minute,
                t.second, _truncate_fraction(t.fraction, notes, path),
                o.minutes)
    if isinstance(value, TLocalDateTime):
        d, t = value.date, value.time
        return ("datetime-local", d.year, d.month, d.day, t.hour, t.minute,
                t.second, _truncate_fraction(t.fraction, notes, path))
    if isinstance(value, TLocalDate):
        return ("date-local", value.year, value.month, value.day)
    if isinstance(value, TLocalTime):
        return ("time-local", value.hour, value.minute, value.second,
                _truncate_fraction(value.fraction, notes, path))
    raise TypeError(f"cannot canonicalize {type(value).__name__}")
