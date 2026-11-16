"""Encode tomlone output into the toml-test typed-JSON form.

The official conformance suite compares a parser's output against a JSON
fixture where every non-container scalar is wrapped as
``{"type": ..., "value": ...}``. Strings and containers appear bare.
"""

from __future__ import annotations

import math

from tomlone._datetimes import (
    LocalDate,
    LocalDateTime,
    LocalTime,
    OffsetDateTime,
)


def encode(value: object) -> object:
    if isinstance(value, dict):
        return {k: encode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [encode(v) for v in value]
    if isinstance(value, bool):
        return {"type": "bool", "value": "true" if value else "false"}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": _float_text(value)}
    if isinstance(value, str):
        return {"type": "string", "value": value}
    if isinstance(value, OffsetDateTime):
        return {"type": "datetime", "value": value.canonical()}
    if isinstance(value, LocalDateTime):
        return {"type": "datetime-local", "value": value.canonical()}
    if isinstance(value, LocalDate):
        return {"type": "date-local", "value": value.canonical()}
    if isinstance(value, LocalTime):
        return {"type": "time-local", "value": value.canonical()}
    raise TypeError(f"cannot encode value of type {type(value).__name__}")


def _float_text(value: float) -> str:
    if math.isnan(value):
        return "nan"
    if math.isinf(value):
        return "inf" if value > 0 else "-inf"
    text = repr(value)
    # Python already renders whole floats with a trailing ".0"; keep the
    # spelling in lockstep with the toml-test float grammar.
    return text
