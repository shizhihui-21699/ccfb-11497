"""Render generator AST nodes into valid TOML 1.0 text."""

from __future__ import annotations

from .ast_nodes import Document, InlineTable, KeyVal, TableHeader

_BASIC_ESCAPES = {
    "\\": "\\\\", '"': '\\"', "\b": "\\b", "\t": "\\t", "\n": "\\n",
    "\f": "\\f", "\r": "\\r",
}


def render_basic(text: str) -> str:
    out = ['"']
    for ch in text:
        if ch in _BASIC_ESCAPES:
            out.append(_BASIC_ESCAPES[ch])
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def render_key(key: str, quoted: bool) -> str:
    if quoted:
        return render_basic(key)
    if key and all(c.isascii() and (c.isalnum() or c in "_-") for c in key):
        return key
    return render_basic(key)


def render_dotted(path: list[str], quoted: bool) -> str:
    return ".".join(render_key(k, quoted) for k in path)


def render_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _render_float(value)
    if isinstance(value, str):
        return render_basic(value)
    if isinstance(value, InlineTable):
        return render_inline(value)
    if isinstance(value, list):
        return render_array(value)
    if isinstance(value, _Raw):
        return value.text
    raise TypeError(f"cannot render {type(value).__name__}")


class _Raw:
    """A value whose TOML spelling is chosen by the generator directly
    (numbers in special bases, datetimes, special strings)."""
    __slots__ = ("text",)

    def __init__(self, text: str) -> None:
        self.text = text


def _render_float(value: float) -> str:
    if value != value:  # NaN
        return "nan"
    if value == float("inf"):
        return "inf"
    if value == float("-inf"):
        return "-inf"
    return repr(value)


def render_array(values: list, depth: int = 0) -> str:
    if not values:
        return "[]"
    # multiline layout when nested or several elements, flat when tiny
    if depth > 0 or len(values) > 3:
        indent = "  " * (depth + 1)
        inner = (",\n" + indent).join(render_scalar(v) for v in values)
        return "[\n" + indent + inner + ",\n" + ("  " * depth) + "]"
    return "[" + ", ".join(render_scalar(v) for v in values) + "]"


def render_inline(table: InlineTable) -> str:
    parts = []
    for kv in table.items:
        value_text = render_scalar(kv.value)
        parts.append(f"{render_dotted(kv.key_path, table.dotted)} = {value_text}")
    return "{ " + ", ".join(parts) + " }"


def render_document(doc: Document) -> str:
    lines: list[str] = []
    for stmt in doc.statements:
        if isinstance(stmt, TableHeader):
            brackets = "[[" if stmt.array else "["
            close = "]]" if stmt.array else "]"
            head = f"{brackets}{render_dotted(stmt.path, stmt.quoted)}{close}"
            if stmt.comment:
                head += "  # " + stmt.comment
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(head)
        elif isinstance(stmt, KeyVal):
            text = f"{render_dotted(stmt.key_path, stmt.quoted_keys)} = {render_scalar(stmt.value)}"
            if stmt.comment:
                text += "  # " + stmt.comment
            lines.append(text)
        else:  # pragma: no cover
            raise TypeError(f"unknown statement {type(stmt).__name__}")
    return "\n".join(lines) + "\n"
