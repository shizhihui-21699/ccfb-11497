"""A small intermediate representation for TOML 1.0 documents.

The fuzzer never splices blind bytes to build "structured" cases: it builds
:mod:`fuzz.ir` documents, renders them to TOML text, and -- crucially -- keeps
the IR around so mutators can operate on syntax units (a value, a key path, a
header) rather than guessing where a token starts and ends.

The IR deliberately covers every TOML 1.0 construct, including the awkward
corners: dotted keys, multiline strings, arrays-of-tables, inline tables and
all four date/time flavours.
"""

from __future__ import annotations

import string
from dataclasses import dataclass, field
from typing import Union


# --------------------------------------------------------------- value nodes
@dataclass
class Int:
    text: str  # already-valid source spelling, e.g. "0xDEAD_BEEF", "-1_000"


@dataclass
class Float:
    text: str  # e.g. "6.626e-34", "inf", "-nan"


@dataclass
class Bool:
    value: bool


@dataclass
class BasicStr:
    value: str          # logical content; escapes are emitted on render
    multiline: bool = False


@dataclass
class LiteralStr:
    value: str
    multiline: bool = False


@dataclass
class DateTime:
    text: str  # pre-validated spelling of any of the four temporal flavours


@dataclass
class Array:
    items: list["Value"] = field(default_factory=list)


@dataclass
class InlineTable:
    # pairs: (key path, value); keys are dotted paths as tuples.
    pairs: list[tuple[tuple[str, ...], "Value"]] = field(default_factory=list)


Value = Union[Int, Float, Bool, BasicStr, LiteralStr, DateTime, Array, InlineTable]


@dataclass
class KeyVal:
    path: tuple[str, ...]
    value: Value


@dataclass
class TableHeader:
    path: tuple[str, ...]
    array: bool = False  # [[name]] when True
    body: list[KeyVal] = field(default_factory=list)


@dataclass
class Document:
    # Root-level key/values come first; then a sequence of table sections.
    root_pairs: list[KeyVal] = field(default_factory=list)
    sections: list[TableHeader] = field(default_factory=list)


# ------------------------------------------------------------------- rendering
_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


def _escape_basic(text: str, multiline: bool) -> str:
    out = []
    for ch in text:
        if ch in _ESCAPES:
            if multiline and ch == "\n":
                out.append(ch)  # literal newlines are fine inside """..."""
            elif multiline and ch == "\r":
                # CR (and bare CRLF) inside a multiline string must still be
                # escaped; emitting a raw \r makes even tolerant parsers fail.
                out.append("\\r")
            else:
                out.append(_ESCAPES[ch])
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)
    return "".join(out)


def _render_key(key: str) -> str:
    # Bare keys when legal; otherwise a basic-quoted key.
    if key and key[0] in (string.ascii_letters + "_") and \
            all(c.isalnum() or c in "_-" for c in key):
        return key
    return '"' + _escape_basic(key, multiline=False) + '"'


def _render_path(path: tuple[str, ...]) -> str:
    return ".".join(_render_key(k) for k in path)


def render_value(v: Value) -> str:
    if isinstance(v, Int):
        return v.text
    if isinstance(v, Float):
        return v.text
    if isinstance(v, Bool):
        return "true" if v.value else "false"
    if isinstance(v, BasicStr):
        if v.multiline:
            return '"""' + _escape_basic(v.value, True) + '"""'
        return '"' + _escape_basic(v.value, False) + '"'
    if isinstance(v, LiteralStr):
        has_ctrl = any(ord(c) < 0x20 or ord(c) == 0x7F for c in v.value)
        if v.multiline:
            # A literal multiline string cannot contain ''' or any raw
            # control character; fall back to a basic multiline string.
            if "'''" not in v.value and not has_ctrl:
                return "'''" + v.value + "'''"
            return '"""' + _escape_basic(v.value, True) + '"""'
        if "'" not in v.value and not has_ctrl:
            return "'" + v.value + "'"
        return '"' + _escape_basic(v.value, False) + '"'
    if isinstance(v, DateTime):
        return v.text
    if isinstance(v, Array):
        if not v.items:
            return "[]"
        rendered = [render_value(i) for i in v.items]
        # Any item containing a newline forces the whole array multiline.
        if any("\n" in r for r in rendered):
            inner = ",\n  ".join(rendered)
            return "[\n  " + inner + ",\n]"
        return "[" + ", ".join(rendered) + "]"
    if isinstance(v, InlineTable):
        inner = ", ".join(
            f"{_render_path(p)} = {render_value(val)}" for p, val in v.pairs
        )
        return "{" + inner + "}"
    raise TypeError(f"unknown value node {v!r}")


def render(doc: Document) -> str:
    lines: list[str] = []
    for kv in doc.root_pairs:
        lines.append(f"{_render_path(kv.path)} = {render_value(kv.value)}")
    for section in doc.sections:
        opener = "[" + ("[" if section.array else "")
        closer = "]" + ("]" if section.array else "")
        lines.append(f"{opener}{_render_path(section.path)}{closer}")
        for kv in section.body:
            lines.append(f"{_render_path(kv.path)} = {render_value(kv.value)}")
        lines.append("")
    return "\n".join(lines)
