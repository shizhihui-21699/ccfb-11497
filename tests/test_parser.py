"""Unit tests pinning parser edge cases found during conformance work."""

import pytest

import tomlone
from tomlone import TOMLDecodeError


def test_basic_scalars():
    doc = tomlone.loads('a = 1\nb = 1.5\nc = true\nd = "s"\n')
    assert doc == {"a": 1, "b": 1.5, "c": True, "d": "s"}


def test_integer_bases_and_underscores():
    doc = tomlone.loads(
        "h = 0xDEADBEEF\no = 0o755\nb = 0b11010110\nd = 5_000_000\n"
    )
    assert doc == {"h": 0xDEADBEEF, "o": 0o755, "b": 0b11010110, "d": 5_000_000}


def test_signed_prefixed_integer_rejected():
    for bad in ["+0x10", "-0b1", "+0o7", "-0xFF"]:
        with pytest.raises(TOMLDecodeError):
            tomlone.loads(f"x = {bad}\n")


def test_leading_zero_rejected():
    for bad in ["03", "03.14", "-03.14", "+007"]:
        with pytest.raises(TOMLDecodeError):
            tomlone.loads(f"x = {bad}\n")


def test_float_forms():
    doc = tomlone.loads(
        "a = 3.1415\nb = -0.01\nc = 6.626e-34\nd = 5e+22\n"
        "e = inf\nf = -inf\ng = nan\n"
    )
    assert doc["a"] == 3.1415
    assert doc["b"] == -0.01
    assert doc["c"] == pytest.approx(6.626e-34)
    assert doc["d"] == 5e22
    assert doc["e"] == float("inf") and doc["f"] == float("-inf")
    assert doc["g"] != doc["g"]  # NaN


def test_float_sign_not_doubled():
    assert tomlone.loads("x = -3.14\n")["x"] == -3.14
    assert tomlone.loads("x = -1E-1\n")["x"] == pytest.approx(-0.1)


def test_space_separated_datetime():
    doc = tomlone.loads("d = 1979-05-27 07:32:00-07:00\n")
    d = doc["d"]
    assert d.offset.minutes == -420
    assert d.canonical() == "1979-05-27T07:32:00-07:00"


def test_datetime_fractional_precision_preserved():
    doc = tomlone.loads("d = 1979-05-27T07:32:00.123456789Z\n")
    assert doc["d"].time.fraction == ".123456789"


def test_invalid_dates_rejected():
    for bad in ["2021-02-30", "2021-13-01", "1990-01-01T25:00:00"]:
        with pytest.raises(TOMLDecodeError):
            tomlone.loads(f"d = {bad}\n")


def test_leap_second_rejected_for_ecosystem_interop():
    # TOML's ABNF text allows second 60, but tomllib/rtoml/pytomlpp all
    # reject it (no datetime type represents it); we match that consensus.
    assert tomlone.loads("t = 01:49:59\n")["t"].second == 59
    for bad in ["01:49:60", "16:14:60", "2066-04-16T16:14:60Z"]:
        with pytest.raises(TOMLDecodeError):
            tomlone.loads(f"t = {bad}\n")


def test_multiline_quote_runs():
    # 4 quotes: one content quote + triple delimiter
    assert tomlone.loads("a = ''''x''''\n") == {"a": "'x'"}
    # 5 quotes: two content + delimiter
    assert tomlone.loads("a = '''''x''''' \n") == {"a": "''x''"}
    # 8 quotes total: opening ''' + two content quotes + closing '''
    assert tomlone.loads("a = ''''''''\n") == {"a": "''"}
    # 6 consecutive quotes with no room for a valid close (corpus shape)
    with pytest.raises(TOMLDecodeError):
        tomlone.loads('a = """x""""""\n')
    with pytest.raises(TOMLDecodeError):
        tomlone.loads("a = '''x''''''\n")


def test_inline_table_dotted_shared_root():
    doc = tomlone.loads("x = {a.b.c = 1, a.b.d = 2}\n")
    assert doc == {"x": {"a": {"b": {"c": 1, "d": 2}}}}


def test_inline_table_cannot_be_extended():
    with pytest.raises(TOMLDecodeError):
        tomlone.loads("x = { a = 1 }\n[x]\nb = 2\n")


def test_inline_table_no_newline():
    with pytest.raises(TOMLDecodeError):
        tomlone.loads("x = {\n  a = 1\n}\n")


def test_inline_table_no_trailing_comma():
    with pytest.raises(TOMLDecodeError):
        tomlone.loads("x = { a = 1, }\n")


def test_table_redefine_rejected():
    with pytest.raises(TOMLDecodeError):
        tomlone.loads("[a]\nb = 1\n[a]\nc = 2\n")


def test_value_then_table_rejected():
    with pytest.raises(TOMLDecodeError):
        tomlone.loads("[a]\nb = 1\n[a.b]\nc = 2\n")


def test_array_of_tables_basic():
    doc = tomlone.loads(
        "[[p]]\nname = \"a\"\n[[p]]\nname = \"b\"\n"
    )
    assert doc == {"p": [{"name": "a"}, {"name": "b"}]}


def test_plain_array_cannot_become_aot():
    with pytest.raises(TOMLDecodeError):
        tomlone.loads("fruit = []\n[[fruit]]\n")


def test_nested_aot_paths_distinct():
    doc = tomlone.loads(
        "[[arr]]\nt = {x = 1}\n[[arr]]\nt = {x = 2}\n"
    )
    assert doc == {"arr": [{"t": {"x": 1}}, {"t": {"x": 2}}]}


def test_statement_requires_newline():
    with pytest.raises(TOMLDecodeError):
        tomlone.loads('a = 1 b = 2\n')
    with pytest.raises(TOMLDecodeError):
        tomlone.loads('first = "x" last = "y"\n')


def test_non_ascii_bare_key_rejected():
    with pytest.raises(TOMLDecodeError):
        tomlone.loads("μ = 1\n")


def test_double_bracket_must_be_adjacent():
    with pytest.raises(TOMLDecodeError):
        tomlone.loads("[ [t]]\n")
    with pytest.raises(TOMLDecodeError):
        tomlone.loads("[[t] ]\n")


def test_del_char_in_comment_rejected():
    with pytest.raises(TOMLDecodeError):
        tomlone.loads("x = 1 # del:\x7f\n")


def test_bom_only_at_start():
    assert tomlone.loads("﻿x = 1\n") == {"x": 1}
    with pytest.raises(TOMLDecodeError):
        tomlone.loads("x = 1\n﻿y = 2\n")


def test_crlf_normalized_in_strings():
    doc = tomlone.loads('a = """x\r\ny"""\n')
    assert doc["a"] == "x\ny"


def test_tab_allowed_in_single_line_strings():
    # regression: TAB (0x09) is legal inside single-line strings
    assert tomlone.loads('a = "x\ty"\n') == {"a": "x\ty"}
    assert tomlone.loads("a = 'x\ty'\n") == {"a": "x\ty"}
    # other C0 controls and DEL remain forbidden
    for bad in ['a = "x\x00y"\n', 'a = "x\x01y"\n', 'a = "x\x7fy"\n']:
        with pytest.raises(TOMLDecodeError):
            tomlone.loads(bad)


def test_space_datetime_requires_exactly_one_space():
    # regression (bypass class): only a single U+0020 separates date/time
    tomlone.loads("d = 1901-02-24 00:20:25\n")  # valid
    for bad in [
        "d = 1901-02-24  00:20:25\n",
        "d = 1901-02-24   00:20:25\n",
        "d = 1901-02-24\t00:20:25\n",
        "8 = [1901-02-24  00:20:25]\n",
    ]:
        with pytest.raises(TOMLDecodeError):
            tomlone.loads(bad)


def test_multiline_line_trim_with_whitespace():
    # regression (false reject): backslash + spaces + newline is a valid trim
    assert tomlone.loads('x = """\\ \n"""\n') == {"x": ""}
    assert tomlone.loads('x = """a\\   \nb"""\n') == {"x": "ab"}
    # backslash + space + non-whitespace is still an invalid escape
    with pytest.raises(TOMLDecodeError):
        tomlone.loads('a = """\n  foo \\ \\n\n  bar"""\n')


def test_float_leading_zero_includes_exponent_form():
    # regression (bypass class): exponent-only floats still need no leading 0
    assert tomlone.loads("x = 0e0\n") == {"x": 0.0}
    assert tomlone.loads("x = 6e2\n") == {"x": 600.0}
    for bad in ["00e0", "01e3", "00e0", "-00e1"]:
        with pytest.raises(TOMLDecodeError):
            tomlone.loads(f"x = {bad}\n")


def test_unicode_escape_rejects_non_hex():
    # regression (bypass class): int(...,16) accepted underscores
    assert tomlone.loads('x = "\\u00e9"\n') == {"x": "é"}
    for bad in [r'x = "\u00_B4"', r'x = "\U00003_B4"', r'x = "\uZZZZ"',
                r'x = "\u00g9"']:
        with pytest.raises(TOMLDecodeError):
            tomlone.loads(bad + "\n")
    # surrogates and out-of-range still rejected
    for bad in [r'x = "\uD800"', r'x = "\U00110000"']:
        with pytest.raises(TOMLDecodeError):
            tomlone.loads(bad + "\n")


def test_hash_is_literal_inside_multiline_string():
    # regression: '#' in a string is content, not a comment
    assert tomlone.loads('a = """x # y"""\n') == {"a": "x # y"}
    # a line-trim backslash cannot skip across a '#' (non-whitespace)
    with pytest.raises(TOMLDecodeError):
        tomlone.loads('1={3="""\\ #\n"""}\n')
    # '#' on a continuation line is kept as content
    doc = 'x={d="""\\\n#\n""",j=2}\n'
    assert tomlone.loads(doc) == {"x": {"d": "#\n", "j": 2}}


def test_dotted_implicit_table_cannot_be_explicitly_defined():
    # regression (bypass class): dotted key makes 'a' an implicit table
    # that a later [a] header must not be able to redefine
    with pytest.raises(TOMLDecodeError):
        tomlone.loads("a.x = 1\n[a]\n")
    with pytest.raises(TOMLDecodeError):
        tomlone.loads("a.b = 1\n[a.b]\n")
    # but a header-created intermediate implicit table stays definable
    assert tomlone.loads("[a.b]\nc = 1\n[a]\n") == {"a": {"b": {"c": 1}}}


def test_float_dot_must_be_adjacent():
    # regression (bypass class): no whitespace around the decimal point
    assert tomlone.loads("x = 3.14\n") == {"x": 3.14}
    for bad in ["t = 0 .1\n", "r = 3 .1\n", "x = 1. 2\n"]:
        with pytest.raises(TOMLDecodeError):
            tomlone.loads(bad)
    # dotted numeric keys remain valid (the dot is a key separator there)
    assert tomlone.loads("1.2 = 3\n") == {"1": {"2": 3}}


def test_leading_zero_with_underscores():
    # regression (bypass class): underscores are separators, so "0_0" is
    # the illegal leading-zero sequence "00"
    assert tomlone.loads("x = 0\n") == {"x": 0}
    assert tomlone.loads("x = 5_000\n") == {"x": 5000}
    assert tomlone.loads("x = 0.5\n") == {"x": 0.5}
    for bad in ["00", "0_0", "0_1", "0_100", "0_0.5", "0_1.2",
                "0_0e0", "0_1e2"]:
        with pytest.raises(TOMLDecodeError):
            tomlone.loads(f"x = {bad}\n")


# --- findings from the independent adversarial audit -----------------------

def test_closed_inline_table_cannot_be_extended_via_header():
    # audit defect #1 (bypass class): descent through a closed inline table
    # to a new sub-key must fail, whether via [table] or [[aot]]
    for bad in [
        "a = {b = 1}\n[a.c]\n",
        "a = {}\n[a.b]\n",
        "a = {b = {}}\n[a.b.c]\n",
        "a = {}\n[[a.b]]\n",
        "t = {x = 1}\n[[t.y]]\n",
        "[[a]]\nt = {x = 1}\n[a.t.y]\n",
    ]:
        with pytest.raises(TOMLDecodeError):
            tomlone.loads(bad)
    # exact-path extension was already rejected and must stay so
    with pytest.raises(TOMLDecodeError):
        tomlone.loads("a = {}\n[a]\n")
    # ordinary (non-inline) implicit tables remain extendable
    assert tomlone.loads("[a.b]\nc = 1\n[a]\n") == {"a": {"b": {"c": 1}}}


def test_inline_quoted_dotted_key_distinct_from_path():
    # audit defect #2: '"a.b"' (one key) vs a.b (dotted path) are distinct
    doc = 'x = {"a.b" = 1, a.b = 2}\n'
    assert tomlone.loads(doc) == {"x": {"a.b": 1, "a": {"b": 2}}}
    # same-named leaves in different subtrees are legal
    assert tomlone.loads("x = {a.y = 1, b.y = 2}\n") == {
        "x": {"a": {"y": 1}, "b": {"y": 2}}
    }
    # genuine conflicts are still caught
    for bad in ['x = {a = 1, "a" = 2}\n', "x = {a.b = 1, a.b = 2}\n",
                "x = {a = 1, a.b = 2}\n", "x = {a.b = 1, a = 2}\n"]:
        with pytest.raises(TOMLDecodeError):
            tomlone.loads(bad)


def test_bom_allowed_inside_strings_but_not_lexical_positions():
    # audit defect #3
    bom = "﻿"
    assert tomlone.loads(f'a = "x{bom}y"\n') == {"a": f"x{bom}y"}
    assert tomlone.loads(f"a = 'x{bom}y'\n") == {"a": f"x{bom}y"}
    assert tomlone.loads(f'a = """x{bom}y"""\n') == {"a": f"x{bom}y"}
    # leading BOM is accepted (stripped); a second/structural BOM rejected
    assert tomlone.loads(bom + "a = 1\n") == {"a": 1}
    with pytest.raises(TOMLDecodeError):
        tomlone.loads(f"a = 1\n{bom}b = 2\n")


def test_finite_float_overflow_rejected():
    # audit defect #4: an out-of-range finite literal must error, not become
    # a silent inf; the inf keyword itself stays legal
    for bad in ["1e309", "9E503", "-1e400", "1e10000"]:
        with pytest.raises(TOMLDecodeError):
            tomlone.loads(f"a = {bad}\n")
    assert tomlone.loads("a = inf\n")["a"] == float("inf")
    assert tomlone.loads("a = 1.7976931348623157e308\n")["a"] == 1.7976931348623157e308


def test_dotted_numeric_key():
    assert tomlone.loads("1.2 = 3\n") == {"1": {"2": 3}}


def test_empty_document():
    assert tomlone.loads("") == {}
    assert tomlone.loads("\n\n# comment\n") == {}
