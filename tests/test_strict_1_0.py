"""Strict TOML 1.0 conformance checks.

Engine landscape (measured, not assumed) on Python 3.15:
- ``tomllib`` (stdlib) implements TOML 1.1.
- ``rtoml`` 0.14 partially implements 1.1 (accepts trailing inline commas,
  multiline inline tables, ``\\x``/``\\e``).
- ``pytomlpp`` 1.1.0 stays strict TOML 1.0 on every probe below.

So the authoritative in-process 1.0 reference is **pytomlpp**, backed by the
official toml-test **v1.0.0** corpus (281 cases, all passing separately).
rtoml is used as a second opinion where it agrees with 1.0; where it follows
1.1, the divergence is documented rather than treated as ground truth.
"""

import pytomlpp
import rtoml

import tomlone
from tomlone import TOMLDecodeError


def _verdict(module, doc):
    try:
        module.loads(doc)
        return True
    except Exception:
        return False


def _matches_pytomlpp(doc):
    """tomlone's accept/reject must match the strict-1.0 reference."""
    strict = _verdict(pytomlpp, doc)
    ours = _verdict(tomlone, doc)
    assert ours == strict, f"tomlone={ours} pytomlpp={strict} on {doc!r}"


# --- rules where rtoml drifted to 1.1: pin to pytomlpp + spec text ---------

def test_inline_table_trailing_comma_rejected():
    # TOML 1.0 explicitly forbids a trailing comma in inline tables.
    _matches_pytomlpp("x = { a = 1, }\n")
    _matches_pytomlpp("x = { a = { b = 1, } }\n")


def test_inline_table_must_be_single_line():
    _matches_pytomlpp("x = {\n  a = 1\n}\n")


# --- audit defect #1: closed inline immutability ---------------------------

def test_closed_inline_not_extendable():
    for bad in [
        "a = {b = 1}\n[a.c]\n",
        "a = {}\n[a.b]\n",
        "a = {b = {}}\n[a.b.c]\n",
        "a = {}\n[[a.b]]\n",
        "t = {x = 1}\n[[t.y]]\n",
        "[[a]]\nt = {x = 1}\n[a.t.y]\n",
    ]:
        _matches_pytomlpp(bad)
        # rtoml agrees here even though it drifted elsewhere
        assert _verdict(rtoml, bad) is False


def test_ordinary_implicit_table_extendable():
    _matches_pytomlpp("[a.b]\nc = 1\n[a]\n")


# --- audit defect #2: quoted dotted keys -----------------------------------

def test_quoted_dotted_key_distinct_from_path():
    _matches_pytomlpp('x = {"a.b" = 1, a.b = 2}\n')
    _matches_pytomlpp("x = {a.y = 1, b.y = 2}\n")
    _matches_pytomlpp('x = {a = 1, "a" = 2}\n')


# --- audit defect #3: BOM ---------------------------------------------------

def test_bom_handling_matches_strict():
    bom = "﻿"
    _matches_pytomlpp(f'a = "x{bom}y"\n')
    _matches_pytomlpp(bom + "a = 1\n")
    _matches_pytomlpp(f"a = 1\n{bom}b = 2\n")


# --- audit defect #4: finite float overflow --------------------------------

def test_finite_float_overflow_rejected():
    for bad in ["1e309", "9E503", "-1e400", "1e10000"]:
        _matches_pytomlpp(f"a = {bad}\n")
    _matches_pytomlpp("a = inf\n")
    _matches_pytomlpp("a = 1.7976931348623157e308\n")


# --- earlier fuzz-discovered rules re-pinned to the strict reference -------

def test_datetime_single_space():
    _matches_pytomlpp("d = 1901-02-24 00:20:25\n")
    _matches_pytomlpp("d = 1901-02-24  00:20:25\n")


def test_leading_zeros():
    for bad in ["00", "0_0", "00e0", "03.14"]:
        _matches_pytomlpp(f"x = {bad}\n")


def test_leap_second():
    for bad in ["01:49:60", "2066-04-16T16:14:60Z"]:
        _matches_pytomlpp(f"t = {bad}\n")


def test_integers_enforced_to_signed_64bit():
    # TOML 1.0 integer range is -(2^63) .. 2^63-1 across every base
    _matches_pytomlpp("x = 9223372036854775807\n")
    _matches_pytomlpp("x = -9223372036854775808\n")
    _matches_pytomlpp("x = 9223372036854775808\n")
    _matches_pytomlpp("x = -9223372036854775809\n")
    _matches_pytomlpp("x = 0x7FFFFFFFFFFFFFFF\n")
    _matches_pytomlpp("x = 0x8000000000000000\n")
    _matches_pytomlpp("x = 0x100100000101001011110101111110111\n")
    # in-range values still accepted
    _matches_pytomlpp("x = 5_000\n")
    _matches_pytomlpp("x = 0o777\n")
    _matches_pytomlpp("x = 0b101\n")
