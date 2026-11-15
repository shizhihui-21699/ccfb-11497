"""Unit tests for parser behaviors that toml-test does not pin down directly,
plus regressions found during differential fuzzing.
"""

from __future__ import annotations

import datetime as dt
import math

import pytest

import ztoml
from ztoml import TOMLDecodeError


def loads(text):
    return ztoml.loads(text)


def rejects(text):
    with pytest.raises(TOMLDecodeError):
        loads(text)


# ------------------------------------------------------------------- scalars
class TestScalars:
    def test_integer_bases(self):
        assert loads("a = 0xDEADBEEF\n") == {"a": 0xDEADBEEF}
        assert loads("a = 0o755\n") == {"a": 0o755}
        assert loads("a = 0b11010110\n") == {"a": 0b11010110}
        assert loads("a = 1_000_000\n") == {"a": 1_000_000}

    def test_integer_leading_zero_rejected(self):
        rejects("a = 01\n")
        rejects("a = 00\n")
        rejects("a = -01\n")
        rejects("a = +01\n")
        rejects("a = 0_1\n")

    def test_signed_non_decimal_rejected(self):
        rejects("a = -0x1\n")
        rejects("a = +0o7\n")
        rejects("a = -0b10\n")

    def test_float_shapes(self):
        assert loads("a = 6.626e-34\n") == {"a": pytest.approx(6.626e-34)}
        assert loads("a = -0.0\n")["a"] == 0.0
        assert math.copysign(1.0, loads("a = -0.0\n")["a"]) < 0
        assert loads("a = inf\n")["a"] == math.inf
        assert math.isnan(loads("a = nan\n")["a"])

    def test_float_leading_zero_rejected(self):
        rejects("a = 03.14\n")

    def test_bool(self):
        assert loads("a = true\nb = false\n") == {"a": True, "b": False}


class TestStrings:
    def test_basic_escapes(self):
        assert loads('a = "tab\\tend\\n"\n') == {"a": "tab\tend\n"}

    def test_unknown_escape_rejected_in_1_0(self):
        rejects('a = "\\e"\n')
        rejects('a = """\\e"""\n')

    def test_literal_string(self):
        assert loads("a = 'raw \\n no escape'\n") == {"a": "raw \\n no escape"}

    def test_multiline_quote_runs(self):
        # Four/five quotes: one/two content quotes then the closing triple.
        # Expectations are anchored to tomllib, the reference implementation.
        assert loads('one = """"one quote""""\n')["one"] == '"one quote"'
        assert loads('two = """""\n"""\n')["two"] == '""\n'
        assert loads('five = """\nx\n"""""\n')["five"] == 'x\n""'

    def test_multiline_crlf_normalized(self):
        assert loads('a = """x\r\ny"""\n') == {"a": "x\ny"}

    def test_bare_cr_rejected(self):
        rejects('a = """x\ry"""\n')
        rejects('a = """\ry"""\n')   # bare CR right after the opener
        rejects('a = """\r"""\n')
        rejects("a = '''\r'''\n")
        rejects('a = "x\ry"\n')

    def test_control_char_rejected_everywhere(self):
        rejects("a = \"x\x00y\"\n")
        rejects("# comment with \x08 ctrl\na = 1\n")


class TestTemporal:
    def test_offset_datetime(self):
        out = loads("a = 1979-05-27T07:32:00-08:00\n")["a"]
        assert out == dt.datetime(1979, 5, 27, 7, 32, 0,
                                  tzinfo=dt.timezone(dt.timedelta(hours=-8)))

    def test_space_separator_and_lowercase(self):
        assert loads("a = 1979-05-27 07:32:00\n")["a"] == \
            dt.datetime(1979, 5, 27, 7, 32, 0)
        assert loads("a = 1979-05-27t07:32:00z\n")["a"].tzinfo == dt.timezone.utc

    def test_seconds_mandatory_in_toml_1_0(self):
        rejects("a = 17:45\n")
        rejects("a = 1987-07-05T17:45\n")

    def test_local_time_no_offset(self):
        rejects("a = 01:02:03Z\n")

    def test_offset_range(self):
        rejects("a = 1985-06-18 17:04:07+12:60\n")

    def test_invalid_calendar(self):
        rejects("a = 2020-02-30\n")
        rejects("a = 2020-13-01\n")


class TestTables:
    def test_aot_basics(self):
        doc = loads("[[a]]\nx = 1\n[[a]]\nx = 2\n")
        assert doc == {"a": [{"x": 1}, {"x": 2}]}

    def test_header_into_aot_last_element(self):
        doc = loads("[[arr]]\n[arr.subtab]\nv = 1\n[[arr]]\n[arr.subtab]\nv = 2\n")
        assert doc == {"arr": [
            {"subtab": {"v": 1}},
            {"subtab": {"v": 2}},
        ]}

    def test_dotted_key_cannot_become_header(self):
        rejects("[fruit]\napple.color = \"red\"\n\n[fruit.apple]\n")

    def test_header_implicit_can_become_header(self):
        # Defining a super-table after a deeper one is legal.
        doc = loads("[x.y.z.w]\na = 1\n[x]\nc = 3\n")
        assert doc == {"x": {"y": {"z": {"w": {"a": 1}}}, "c": 3}}

    def test_inline_table_immutable(self):
        rejects("a = {}\n[a.b]\nx = 1\n")
        rejects("a.b = 1\na = {}\n")

    def test_inline_duplicate_dotted(self):
        rejects("tab = { fruit = { apple.color = \"red\" }, "
                "fruit.apple.texture = { smooth = true } }\n")

    def test_duplicate_simple_key(self):
        rejects("a = 1\na = 2\n")

    def test_multiline_string_not_a_key(self):
        rejects('"""k""" = 1\n')


class TestKeys:
    def test_bare_key_shapes(self):
        # TOML 1.0 ABNF: unquoted-key = 1*( ALPHA / DIGIT / "-" / "_" ), with
        # no restriction on the first character.
        assert loads("3 = 1\n") == {"3": 1}
        assert loads("-4g = 1\n") == {"-4g": 1}
        assert loads("- = 1\n") == {"-": 1}
        assert loads("_a = 1\n") == {"_a": 1}
        assert loads("a-b_c = 1\n") == {"a-b_c": 1}

    def test_bare_key_rejects_punctuation(self):
        # Only ALPHA / DIGIT / "-" / "_" are legal bare-key characters.
        rejects("bare!key = 1\n")
        rejects("! = 1\n")

    def test_quoted_key_with_space(self):
        assert loads('"a b" = 1\n') == {"a b": 1}
