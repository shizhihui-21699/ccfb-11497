"""Strict parsers for the bare-token scalar grammars of TOML 1.0.

Every function takes the exact text of a BARE token and either returns a
Python value or raises :class:`TOMLDecodeError`. The datetimes are fully
validated (ranges, leap-year rules) through ``datetime.date`` construction.
"""

from __future__ import annotations

import datetime as _dt
import math

from ._datetimes import LocalDate, LocalDateTime, LocalTime, Offset, OffsetDateTime
from ._errors import TOMLDecodeError

_DIGITS = set("0123456789")
_HEX = set("0123456789abcdefABCDEF")
_OCT = set("01234567")
_BIN = set("01")
_INT_PREFIXES = {"x": (_HEX, 16), "o": (_OCT, 8), "b": (_BIN, 2)}

# TOML 1.0 defines every integer as a signed 64-bit value. Python ints are
# arbitrary precision, so we enforce the range explicitly rather than silently
# accepting values no conforming (fixed-width) parser can represent.
INT64_MIN = -(2 ** 63)
INT64_MAX = 2 ** 63 - 1


def _check_int64(value: int, text: str) -> None:
    if not (INT64_MIN <= value <= INT64_MAX):
        raise TOMLDecodeError(f"integer out of 64-bit range: {text!r}")


def parse_scalar(text: str) -> object:
    """Classify and parse a BARE token's text."""
    ch = text[0]
    if ch in _DIGITS:
        return _parse_number_or_date(text)
    if text == "true":
        return True
    if text == "false":
        return False
    if text in ("inf", "nan", "+inf", "-inf", "+nan", "-nan"):
        return _parse_special_float(text)
    if ch in "-+" and len(text) > 1 and text[1] in _DIGITS:
        return _parse_number_or_date(text)
    raise TOMLDecodeError(f"invalid value {text!r}")


# --------------------------------------------------------------------------
# numbers
# --------------------------------------------------------------------------

def _parse_number_or_date(text: str) -> object:
    # Datetimes always contain '-', ':' or 'T'/'t'/'Z'/'z' and start with a
    # four-digit year. Integers use '-' only as sign / base-independent.
    if len(text) >= 5 and text[0:4].isdigit() and text[4] == "-":
        return parse_datetime(text)
    # Bare local time HH:MM:SS[.frac]
    if len(text) >= 8 and text[2] == ":" and text[5] == ":":
        return _parse_time(text)
    if "inf" in text or "nan" in text:
        return _parse_special_float(text)
    # prefixed integers (0x/0o/0b) must be tried before float detection,
    # otherwise hex digits such as 'e' look like an exponent marker
    body = text[1:] if text[:1] in "+-" else text
    if len(body) >= 2 and body[0] == "0" and body[1] in "xob":
        return parse_integer(text)
    if any(c in text for c in ".eE"):
        return parse_float(text)
    return parse_integer(text)


def _check_underscores(body: str) -> None:
    if "__" in body or body.startswith("_") or body.endswith("_"):
        raise TOMLDecodeError(f"invalid underscore placement in {body!r}")


def parse_integer(text: str) -> int:
    sign = 1
    body = text
    had_sign = body[:1] in "+-"
    if had_sign and body[0] == "-":
        sign = -1
    if had_sign:
        body = body[1:]
    if not body:
        raise TOMLDecodeError(f"invalid integer {text!r}")

    if len(body) >= 2 and body[0] == "0" and body[1] in "xob":
        if had_sign:
            raise TOMLDecodeError(
                f"signs are not allowed on base-prefixed integers: {text!r}"
            )
        prefix = body[1]
        digits = body[2:]
        if not digits:
            raise TOMLDecodeError(f"invalid integer {text!r}")
        _check_underscores(digits)
        allowed, base = _INT_PREFIXES[prefix]
        digits = digits.replace("_", "")
        if any(c not in allowed for c in digits):
            raise TOMLDecodeError(f"invalid digit for base {base} integer {text!r}")
        value = sign * int(digits, base)
        _check_int64(value, text)
        return value

    _check_underscores(body)
    digits = body.replace("_", "")
    if not digits or any(c not in _DIGITS for c in digits):
        raise TOMLDecodeError(f"invalid integer {text!r}")
    # leading zero: underscores are only readability separators, so the
    # digit sequence "0_0" is really "00" and still illegal; only a lone "0"
    # (or "0" followed by non-digit grammar) is allowed
    if len(digits) > 1 and digits[0] == "0":
        raise TOMLDecodeError(f"leading zero not allowed in integer {text!r}")
    value = sign * int(digits, 10)
    _check_int64(value, text)
    return value


def parse_float(text: str) -> float:
    sign = 1
    body = text
    if body[0] in "+-":
        if body[0] == "-":
            sign = -1
        body = body[1:]
    if not body:
        raise TOMLDecodeError(f"invalid float {text!r}")

    # split exponent
    exp_part = ""
    has_exp = False
    for i, c in enumerate(body):
        if c in "eE":
            has_exp = True
            exp_part = body[i + 1:]
            body = body[:i]
            break
    if has_exp and exp_part in ("", "+", "-"):
        raise TOMLDecodeError(f"invalid exponent in float {text!r}")
    if has_exp:
        if exp_part[0] in "+-":
            exp_digits = exp_part[1:]
        else:
            exp_digits = exp_part
        _check_underscores(exp_digits)
        exp_digits = exp_digits.replace("_", "")
        if not exp_digits or any(c not in _DIGITS for c in exp_digits):
            raise TOMLDecodeError(f"invalid exponent in float {text!r}")

    if body.count(".") > 1:
        raise TOMLDecodeError(f"multiple decimal points in float {text!r}")
    if "." in body:
        whole, frac = body.split(".")
        if not whole or not frac:
            raise TOMLDecodeError(f"decimal point must be surrounded by digits: {text!r}")
        _check_float_whole(whole, text)
        _check_underscores(frac)
        if any(c not in _DIGITS | {"_"} for c in whole + frac):
            raise TOMLDecodeError(f"invalid float {text!r}")
    else:
        # exponent-only float, e.g. 6e2 or the invalid 00e0: the integer
        # portion still obeys the no-leading-zero rule
        _check_float_whole(body, text)
        if any(c not in _DIGITS | {"_"} for c in body):
            raise TOMLDecodeError(f"invalid float {text!r}")

    clean = text[1:] if text[0] in "+-" else text
    result = float(("-" if sign < 0 else "") + clean.replace("_", ""))
    # A *finite* literal that overflows binary64 is out of range and must be
    # rejected. Python's float() silently yields inf; the literal "inf"
    # keyword is handled by _parse_special_float and never reaches here.
    if math.isinf(result):
        raise TOMLDecodeError(f"float value out of range: {text!r}")
    return result


def _check_float_whole(whole: str, original: str) -> None:
    """Validate the integer portion of a float: underscores + no leading zero."""
    _check_underscores(whole)
    digits = whole.replace("_", "")
    if len(digits) > 1 and digits[0] == "0":
        raise TOMLDecodeError(f"leading zero not allowed in float {original!r}")


def _parse_special_float(text: str) -> float:
    mapping = {
        "inf": float("inf"),
        "+inf": float("inf"),
        "-inf": float("-inf"),
        "nan": float("nan"),
        "+nan": float("nan"),
        "-nan": float("nan"),
    }
    if text not in mapping:
        raise TOMLDecodeError(f"invalid special float {text!r}")
    return mapping[text]


# --------------------------------------------------------------------------
# dates and times
# --------------------------------------------------------------------------

def _exact_int(text: str, what: str) -> int:
    if not text or not text.isdigit():
        raise TOMLDecodeError(f"invalid {what}: {text!r}")
    return int(text)


def _parse_fraction(text: str) -> str:
    # text includes leading '.'
    digits = text[1:]
    if not digits or not digits.isdigit():
        raise TOMLDecodeError(f"invalid fractional seconds {text!r}")
    return text


def _parse_time(text: str) -> LocalTime:
    # HH:MM:SS[.frac]
    parts = text.split(":")
    if len(parts) != 3:
        raise TOMLDecodeError(f"invalid local time {text!r}")
    hh, mm, rest = parts
    frac = None
    if "." in rest:
        secs, frac_text = rest.split(".", 1)
        frac = _parse_fraction("." + frac_text)
    else:
        secs = rest
    hour, minute, second = (
        _exact_int(hh, "hour"),
        _exact_int(mm, "minute"),
        _exact_int(secs, "second"),
    )
    if len(hh) != 2 or len(mm) != 2 or len(secs) != 2:
        raise TOMLDecodeError(f"time components must be two digits: {text!r}")
    # TOML 1.0's ABNF nominally allows second 60 (leap second), but every
    # reference implementation (tomllib/rtoml/pytomlpp) rejects it because no
    # standard datetime type can represent it. Rejecting 60 keeps values
    # round-trippable and matches the ecosystem consensus, which is the
    # interop guarantee this parser is built for.
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        raise TOMLDecodeError(f"time component out of range: {text!r}")
    return LocalTime(hour, minute, second, frac)


def _parse_date(text: str) -> LocalDate:
    parts = text.split("-")
    if len(parts) != 3 or any(len(p) != 4 if i == 0 else len(p) != 2
                             for i, p in enumerate(parts)):
        raise TOMLDecodeError(f"invalid local date {text!r}")
    year, month, day = (_exact_int(p, "date component") for p in parts)
    try:
        _dt.date(year, month, day)  # range + leap-year validation
    except ValueError as exc:
        raise TOMLDecodeError(f"date out of range: {text!r}") from exc
    return LocalDate(year, month, day)


def _parse_offset(text: str) -> Offset:
    if text == "Z" or text == "z":
        return Offset(0)
    if len(text) != 6 or text[0] not in "+-" or text[3] != ":":
        raise TOMLDecodeError(f"invalid offset {text!r}")
    sign = 1 if text[0] == "+" else -1
    hh, mm = text[1:3], text[4:6]
    hour = _exact_int(hh, "offset hour")
    minute = _exact_int(mm, "offset minute")
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise TOMLDecodeError(f"offset out of range: {text!r}")
    return Offset.from_parts(sign, hour, minute)


def parse_datetime(text: str) -> object:
    original = text
    # Find the date component (first 10 chars: YYYY-MM-DD).
    if len(text) < 10:
        raise TOMLDecodeError(f"invalid datetime {original!r}")
    date = _parse_date(text[:10])
    rest = text[10:]
    if rest == "":
        return date

    # time portion begins with T/t/space separator
    if rest[0] not in ("T", "t", " "):
        raise TOMLDecodeError(f"invalid datetime separator in {original!r}")
    rest = rest[1:]
    if rest == "":
        raise TOMLDecodeError(f"truncated datetime {original!r}")

    # split time from offset; offset starts at last +/Z/z (minus is ambiguous
    # with nothing in time, so scan for + or Z or trailing -hh:mm)
    time_text, offset = _split_time_offset(rest, original)
    time = _parse_time(time_text)
    if offset is None:
        return LocalDateTime(date, time)
    return OffsetDateTime(date, time, _parse_offset(offset))


def _split_time_offset(rest: str, original: str) -> tuple[str, str | None]:
    # Time grammar is fixed-width up to the seconds: HH:MM:SS (8 chars),
    # optionally followed by .frac. Only then may an offset appear.
    if len(rest) < 8:
        raise TOMLDecodeError(f"invalid time in datetime {original!r}")
    idx = 8
    if len(rest) > idx and rest[idx] == ".":
        idx += 1
        while idx < len(rest) and rest[idx].isdigit():
            idx += 1
    time_text = rest[:idx]
    offset_text = rest[idx:]
    if offset_text == "":
        return time_text, None
    return time_text, offset_text
