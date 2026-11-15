"""Parsing of TOML scalar values and the two container value types.

Scalars handled here: booleans, integers (decimal/hex/oct/binary, with
underscores), floats (including inf/nan) and the four date/time flavours.
Containers: arrays and inline tables (their key/value syntax is shared with
the top-level document via :class:`ztoml.parser.DocumentParser`).
"""

from __future__ import annotations

import datetime as _dt
import re

from .errors import TOMLDecodeError
from .scanner import Scanner

# --------------------------------------------------------------------- regexes
# Integer bodies: underscores may appear between (but never adjacent to)
# digits; sign is consumed by the caller.  Hex permits a-f as well as A-F.
_DEC_INT = re.compile(r"[0-9](?:_?[0-9])*$")
_HEX_INT = re.compile(r"[0-9A-Fa-f](?:_?[0-9A-Fa-f])*$")
_OCT_INT = re.compile(r"[0-7](?:_?[0-7])*$")
_BIN_INT = re.compile(r"[01](?:_?[01])*$")

# Floats: a decimal integer part, optional frac and/or exponent.
_FLOAT_CORE = re.compile(
    r"[0-9](?:_?[0-9])*"
    r"(?:"
    r"(?:\.[0-9](?:_?[0-9])*)"
    r"(?:[eE][+-]?[0-9](?:_?[0-9])*)?"
    r"|"
    r"[eE][+-]?[0-9](?:_?[0-9])*"
    r")$"
)

# Date/times as specified in TOML 1.0 (the separator in a date-time may be
# a space, T or t; a UTC offset may use Z or z).
_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})$")
# Local time per the strict TOML 1.0 ABNF: seconds are MANDATORY.  (TOML 1.1
# makes seconds optional; CPython's tomllib already accepts the 1.1 form, so
# "HH:MM" without seconds is a documented tomllib divergence, not a ztoml
# bug.  See fuzz/known_divergences.md.)
_TIME = re.compile(r"(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?$")
_DATETIME = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[Tt ]"
    r"(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?"
    r"(?:[Zz]|([+-]\d{2}):(\d{2}))?$"
)

# Characters that terminate a value token.  A space only terminates "ordinary"
# values; datetimes legitimately contain one (date<space>time), which is
# handled specially in _read_bare_token.
_BARE_STOP = set(" \t\r\n#,]}")

# Shape of a date-time prefix (used to keep scanning across its inner space).
# A space counts as a separator only when a time digit follows it.
_DATETIME_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[Tt]| (?=\d))")


def _read_bare_token(sc: Scanner) -> str:
    start = sc.pos
    line, col = sc.line, sc.col
    # A date-time's inner separator (T, t or space) must not terminate the
    # token.  Only extend across the separator when a time component actually
    # follows; a plain date ("1979-05-27 # ...") keeps the ordinary rule and
    # stops at the first space.
    head = sc.src[sc.pos:sc.pos + 12]
    if _DATETIME_PREFIX.match(head):
        for _ in range(11):  # consume through the separator
            sc.advance()
        while not sc.eof() and sc.peek() not in _BARE_STOP:
            sc.advance()
    else:
        while not sc.eof() and sc.peek() not in _BARE_STOP:
            sc.advance()
    token = sc.src[start:sc.pos]
    if not token:
        raise sc.error("expected value")
    # Attach the token's start location to errors.
    sc._tok_line, sc._tok_col = line, col
    return token


def _token_error(sc: Scanner, message: str) -> TOMLDecodeError:
    line = getattr(sc, "_tok_line", sc.line)
    col = getattr(sc, "_tok_col", sc.col)
    return TOMLDecodeError(message, line, col)


# ===================================================================== scalars
def parse_bool(token: str, sc: Scanner) -> bool:
    if token == "true":
        return True
    if token == "false":
        return False
    raise _token_error(sc, f"invalid value {token!r}")


def parse_integer(token: str, sc: Scanner) -> int:
    neg = False
    body = token
    if body[0] in "+-":
        neg = body[0] == "-"
        body = body[1:]
    if not body:
        raise _token_error(sc, "invalid integer")
    try:
        if body.startswith(("0x", "0o", "0b")):
            # TOML 1.0: signs are not allowed on hex/octal/binary integers.
            if neg or token[0] == "+":
                raise ValueError
        if body.startswith("0x"):
            digits = body[2:]
            if not _HEX_INT.match(digits):
                raise ValueError
            value = int(digits.replace("_", ""), 16)
        elif body.startswith("0o"):
            digits = body[2:]
            if not _OCT_INT.match(digits):
                raise ValueError
            value = int(digits.replace("_", ""), 8)
        elif body.startswith("0b"):
            digits = body[2:]
            if not _BIN_INT.match(digits):
                raise ValueError
            value = int(digits.replace("_", ""), 2)
        else:
            # Decimal: the regex enforces underscore placement; an explicit
            # check bans leading zeros ("01", "0_1") and keeps the lone "0".
            if not _DEC_INT.match(body):
                raise ValueError
            if len(body) > 1 and body[0] == "0":
                raise ValueError
            value = int(body.replace("_", ""), 10)
    except ValueError:
        raise _token_error(sc, f"invalid integer {token!r}") from None
    return -value if neg else value


def parse_float(token: str, sc: Scanner) -> float:
    if token in ("inf", "+inf"):
        return float("inf")
    if token == "-inf":
        return float("-inf")
    if token in ("nan", "+nan", "-nan"):
        # Python has no signed-nan concept worth preserving; TOML only needs
        # the value to round-trip as NaN.
        return float("nan")
    body = token
    if body[0] in "+-":
        body = body[1:]
    if not _FLOAT_CORE.match(body):
        raise _token_error(sc, f"invalid float {token!r}")
    # Leading zero: "0." and "0e" are fine, "03.14" is not.
    int_part = body.split(".", 1)[0].split("e", 1)[0].split("E", 1)[0]
    if len(int_part.replace("_", "")) > 1 and int_part[0] == "0":
        raise _token_error(sc, f"invalid float {token!r}: leading zero")
    return float(token.replace("_", ""))


def _build_frac(frac: str | None) -> int:
    if not frac:
        return 0
    # datetime accepts microsecond precision; truncate longer fractions.
    return int((frac + "000000")[:6])


def _check_clock(h: int, mi: int, s: int, sc: Scanner, token: str) -> None:
    if not (0 <= h <= 23 and 0 <= mi <= 59 and 0 <= s <= 59):
        raise _token_error(sc, f"invalid time {token!r}")


def _check_offset(off_h: str | None, off_m: str | None,
                  sc: Scanner, token: str) -> _dt.timezone | None:
    if off_h is None:
        return None
    hours, minutes = int(off_h[1:]), int(off_m)
    if hours > 23 or minutes > 59:
        raise _token_error(sc, f"invalid timezone offset in {token!r}")
    delta = _dt.timedelta(hours=hours, minutes=minutes)
    if off_h.startswith("-"):
        delta = -delta
    return _dt.timezone(delta)


def parse_datetime(token: str, sc: Scanner):
    m = _DATETIME.match(token)
    if m:
        (y, mo, d, h, mi, s, frac, off_h, off_m) = m.groups()
        try:
            _check_clock(int(h), int(mi), int(s), sc, token)
            if token.endswith(("Z", "z")):
                tz = _dt.timezone.utc
            else:
                tz = _check_offset(off_h, off_m, sc, token)
            return _dt.datetime(int(y), int(mo), int(d),
                                int(h), int(mi), int(s),
                                _build_frac(frac), tz)
        except ValueError:
            raise _token_error(sc, f"invalid datetime {token!r}") from None

    m = _DATE.match(token)
    if m:
        try:
            return _dt.date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            raise _token_error(sc, f"invalid date {token!r}") from None

    m = _TIME.match(token)
    if m:
        (h, mi, s, frac) = m.groups()
        try:
            _check_clock(int(h), int(mi), int(s), sc, token)
            return _dt.time(int(h), int(mi), int(s), _build_frac(frac))
        except ValueError:
            raise _token_error(sc, f"invalid time {token!r}") from None

    raise _token_error(sc, f"invalid value {token!r}")


def parse_bare_value(sc: Scanner):
    """Parse a value that does not start with a quote or ``[`` / ``{``."""
    token = _read_bare_token(sc)
    if token in ("true", "false"):
        return parse_bool(token, sc)
    if token in ("inf", "+inf", "-inf", "nan", "+nan", "-nan"):
        return parse_float(token, sc)
    # Prefix-detect integers and floats before datetimes: "1979-05-27" would
    # otherwise be tempting to misread, and things like "0x" are handled here.
    first = token[0]
    if first in "+-0123456789":
        kind = _classify_number(token)
        if kind == "int":
            return parse_integer(token, sc)
        if kind == "float":
            return parse_float(token, sc)
        if kind == "datetime":
            return parse_datetime(token, sc)
    # Datetimes may start with a digit only; anything else reaching here is
    # invalid (e.g. a bare word that is not true/false).
    raise _token_error(sc, f"invalid value {token!r}")


def _classify_number(token: str) -> str:
    body = token[1:] if token[0] in "+-" else token
    if not body:
        return "bad"
    # Pre-fixed integer bases.
    if body[:2] in ("0x", "0o", "0b"):
        return "int"
    if any(ch in body for ch in (".", "e", "E")):
        # A date contains "-" and ":", never "." alone as a float decimal
        # point; decide via regex attempts in parse order.
        if re.match(r"^\d{4}-\d{2}-\d{2}", body) or re.match(r"^\d{2}:\d{2}", body):
            return "datetime"
        return "float"
    if "-" in body or ":" in body:
        return "datetime"
    return "int"
