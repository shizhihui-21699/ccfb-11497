"""ztoml -- a from-scratch TOML 1.0 parser."""

from __future__ import annotations

import datetime as _dt
import json as _json
from pathlib import Path as _Path

from .errors import TOMLDecodeError, TOMLError
from .parser import DocumentParser

__all__ = ["loads", "load", "TOMLError", "TOMLDecodeError", "to_tomltest_json"]


def loads(text: str) -> dict:
    """Parse TOML source text into plain Python dicts/lists/scalars."""
    if isinstance(text, bytes):
        text = text.decode("utf-8")
    return DocumentParser(text).parse()


def load(fp) -> dict:
    """Parse TOML from a file-like object (opened in text or binary mode)."""
    data = fp.read()
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    return loads(data)


# ------------------------------------------------------------- toml-test JSON
_TYPE_MAP = {
    _dt.datetime: "datetime",       # offset datetimes carry tzinfo
    _dt.date: "date-local",
    _dt.time: "time-local",
}


def _describe(value):
    """Convert a parsed value into the toml-test JSON description shape."""
    if isinstance(value, bool):
        return {"type": "bool", "value": "true" if value else "false"}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        if value != value:  # NaN
            desc = "nan"
        elif value == float("inf"):
            desc = "inf"
        elif value == float("-inf"):
            desc = "-inf"
        else:
            desc = repr(value)
        return {"type": "float", "value": desc}
    if isinstance(value, str):
        return {"type": "string", "value": value}
    if isinstance(value, _dt.datetime):
        kind = "datetime" if value.tzinfo is not None else "datetime-local"
        return {"type": kind, "value": _format_datetime(value)}
    if isinstance(value, _dt.date):
        return {"type": "date-local", "value": value.isoformat()}
    if isinstance(value, _dt.time):
        return {"type": "time-local", "value": _format_time(value)}
    if isinstance(value, list):
        return [_describe(v) for v in value]
    if isinstance(value, dict):
        return {k: _describe(v) for k, v in value.items()}
    raise TypeError(f"cannot describe value of type {type(value)!r}")


def _format_time(t: _dt.time) -> str:
    base = t.strftime("%H:%M:%S")
    if t.microsecond:
        base += f".{t.microsecond:06d}".rstrip("0")
    if t.tzinfo is not None:
        off = t.utcoffset()
        total = int(off.total_seconds())
        # A zero offset renders as "Z" per the toml-test JSON description.
        if total == 0:
            base += "Z"
            return base
        sign = "+" if total > 0 else "-"
        total = abs(total)
        base += f"{sign}{total // 3600:02d}:{(total % 3600) // 60:02d}"
    return base


def _format_datetime(dt: _dt.datetime) -> str:
    date_part = dt.date().isoformat()
    time_part = _format_time(dt.timetz().replace(tzinfo=dt.tzinfo))
    return f"{date_part}T{time_part}"


def to_tomltest_json(value: dict) -> str:
    """Serialize a parsed document using the toml-test JSON description."""
    return _json.dumps(_describe(value), ensure_ascii=False, indent=2)
