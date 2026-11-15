"""TOML 1.0 document parser: key/value pairs, tables and arrays of tables.

Semantic table bookkeeping follows the TOML 1.0 spec closely.  Every table
node carries provenance flags instead of a global path registry, so equal
paths inside distinct elements of an array of tables stay independent:

* ``implicit``    -- not yet itself the target of a ``[table]`` header;
* ``dotted``      -- created implicitly by a dotted key (may never become the
  target of a later header);
* ``from_inline`` -- created inside an inline table (immutable afterwards;
  headers and dotted keys may not extend it).
"""

from __future__ import annotations

from .errors import TOMLDecodeError
from .scanner import BOM, Scanner
from .values import parse_bare_value

_IMPLICIT = "__ztoml_implicit__"


class _Table(dict):
    """A TOML table.

    Provenance matters for the (subtle) TOML redefinition rules:

    * ``implicit``  -- not yet itself the target of a ``[table]`` header;
    * ``dotted``    -- created implicitly by a dotted key (such a table may
      never become the target of a later header);
    * ``from_inline`` -- created inside an inline table (immutable afterwards;
      headers may not extend it).
    """

    def __init__(self, implicit: bool = False, dotted: bool = False,
                 from_inline: bool = False) -> None:
        super().__init__()
        self.implicit = implicit
        self.dotted = dotted
        self.from_inline = from_inline

    def to_plain(self) -> dict:
        out = {}
        for k, v in self.items():
            if isinstance(v, _Table):
                out[k] = v.to_plain()
            elif isinstance(v, _Aot):
                out[k] = [item.to_plain() if isinstance(item, _Table) else
                          _plain_deep(item) for item in v]
            else:
                out[k] = v
        return out


class _Aot(list):
    """An array of tables (plain list at the public API boundary)."""


def _plain_deep(value):
    if isinstance(value, _Table):
        return value.to_plain()
    if isinstance(value, _Aot):
        return [_plain_deep(v) for v in value]
    if isinstance(value, list):
        return [_plain_deep(v) for v in value]
    return value


class DocumentParser:
    def __init__(self, text: str) -> None:
        self.sc = Scanner(text)
        self.root = _Table(implicit=False)
        self.current = self.root

    # ============================================================== entrypoint
    def parse(self) -> dict:
        sc = self.sc
        # A single UTF-8 BOM (U+FEFF) is permitted only at the very start.
        if sc.peek() == BOM:
            sc.advance()
        sc.skip_blank_lines()
        while not sc.eof():
            if sc.peek() == "[":
                self._parse_header()
            else:
                self._parse_keyval(self.current)
            sc.skip_blank_lines()
        return self.root.to_plain()

    # ================================================================ headers
    def _parse_header(self) -> None:
        sc = self.sc
        is_aot = sc.starts("[[")
        if is_aot:
            sc.advance(); sc.advance()
        else:
            sc.advance()
        sc.skip_inline_ws()
        path = self._parse_key_path(allow_dotted=True)
        sc.skip_inline_ws()
        if is_aot:
            sc.expect("]"); sc.expect("]")
        else:
            sc.expect("]")
        sc.skip_inline_ws()
        if not sc.eof() and sc.peek() not in ("\n", "\r"):
            raise sc.error("expected newline after table header")
        if not sc.eof():
            sc.consume_eol()

        if is_aot:
            self.current = self._open_aot(path)
        else:
            self.current = self._open_table(path)

    def _open_table(self, path: tuple[str, ...]) -> _Table:
        # Redefinition is decided entirely by node-level provenance; a global
        # path set cannot distinguish equal paths inside distinct elements of
        # an array of tables (e.g. each [[arr]] carrying its own [arr.subtab]).
        node = self.root
        for key in path:
            sub = node.get(key)
            if sub is None:
                # Intermediate tables of a header are implicit.  They may
                # later become header targets themselves, but dotted-key
                # tables may not (that provenance is tracked separately).
                sub = _Table(implicit=True)
                node[key] = sub
                node = sub
                continue
            if isinstance(sub, _Table):
                if sub.from_inline:
                    raise self.sc.error(
                        f"cannot extend inline table {key!r}: inline tables are immutable"
                    )
                node = sub
                continue
            if isinstance(sub, _Aot):
                # A header traversing an AOT addresses its last element.
                node = sub[-1]
                continue
            raise self.sc.error(
                f"duplicate key {key!r}: value already exists"
            )
        # Final resolution: only a fresh implicit table (created by a header
        # path) may become the target.  A table born of a dotted key, or an
        # already explicit one, is a redefinition.
        if node is self.root:
            return node
        if node.from_inline or not node.implicit:
            raise self.sc.error(f"table {_fmt(path)} defined more than once")
        if node.dotted:
            raise self.sc.error(
                f"table {_fmt(path)} already created implicitly by a dotted key"
            )
        node.implicit = False
        return node

    def _open_aot(self, path: tuple[str, ...]) -> _Table:
        parent_path, leaf = path[:-1], path[-1]
        parent = self._walk_to_container(parent_path)
        existing = parent.get(leaf)
        if existing is None:
            aot = _Aot()
            parent[leaf] = aot
        elif isinstance(existing, _Aot):
            # Re-appending to an existing array of tables is the whole point
            # of the [[name]] syntax.
            aot = existing
        elif isinstance(existing, _Table):
            if existing.from_inline:
                raise self.sc.error(
                    f"cannot append to {leaf!r}: path is an (immutable) inline table"
                )
            raise self.sc.error(
                f"duplicate key {leaf!r}: table already exists"
            )
        else:
            raise self.sc.error(
                f"duplicate key {leaf!r}: value already exists"
            )
        new_table = _Table(implicit=False)
        aot.append(new_table)
        return new_table

    def _walk_to_container(self, path: tuple[str, ...]) -> _Table:
        """Walk/create header-implicit tables along ``path`` for an AOT header."""
        node = self.root
        for key in path:
            sub = node.get(key)
            if sub is None:
                sub = _Table(implicit=True)
                node[key] = sub
                node = sub
            elif isinstance(sub, _Table):
                if sub.from_inline:
                    raise self.sc.error(
                        f"cannot extend inline table {key!r}: inline tables are immutable"
                    )
                node = sub
            elif isinstance(sub, _Aot):
                node = sub[-1]
            else:
                raise self.sc.error(
                    f"duplicate key {key!r}: value already exists"
                )
        return node

    # ================================================================ keyvals
    def _parse_key_path(self, allow_dotted: bool) -> tuple[str, ...]:
        sc = self.sc
        parts = [sc.parse_key()]
        if allow_dotted:
            while True:
                sc.skip_inline_ws()
                if sc.peek() != ".":
                    break
                sc.advance()
                sc.skip_inline_ws()
                parts.append(sc.parse_key())
        return tuple(parts)

    def _parse_keyval(self, table: _Table) -> None:
        sc = self.sc
        path = self._parse_key_path(allow_dotted=True)
        sc.skip_ws()
        sc.expect("=")
        sc.skip_ws()
        value = self._parse_value()
        sc.skip_inline_ws()
        if not sc.eof() and sc.peek() not in ("\n", "\r"):
            raise sc.error("expected newline after key/value pair")
        if not sc.eof():
            sc.consume_eol()
        self._assign_dotted(table, path, value)

    def _assign_dotted(self, table: _Table, path: tuple[str, ...], value) -> None:
        node = table
        for key in path[:-1]:
            sub = node.get(key)
            if sub is None:
                # Tables born from a dotted key: they can never become the
                # target of a [table] header later.
                sub = _Table(implicit=True, dotted=True)
                node[key] = sub
                node = sub
            elif isinstance(sub, _Table):
                # Dotted keys may only extend implicit tables (including ones
                # created by an earlier dotted key); explicit tables and
                # anything originating from an inline table are off-limits.
                if not sub.implicit or sub.from_inline:
                    raise self.sc.error(
                        f"duplicate key {key!r}: table already explicitly defined"
                    )
                node = sub
            elif isinstance(sub, _Aot):
                # Dotted keys may not reach into an array of tables; only a
                # header may address its last element.
                raise self.sc.error(
                    f"cannot add key {key!r}: path is an array of tables"
                )
            else:
                raise self.sc.error(
                    f"duplicate key {key!r}: value already exists"
                )
        leaf = path[-1]
        if leaf in node:
            raise self.sc.error(f"duplicate key {leaf!r}")
        node[leaf] = value

    # ================================================================= values
    def _parse_value(self):
        sc = self.sc
        c = sc.peek()
        if c == "":
            raise sc.error("expected value, found end of input")
        if c == "[":
            return self._parse_array()
        if c == "{":
            return self._parse_inline_table()
        if c == '"':
            return sc.parse_basic_string()
        if c == "'":
            return sc.parse_literal_string()
        return parse_bare_value(sc)

    def _parse_array(self):
        sc = self.sc
        sc.expect("[")
        out = []
        while True:
            sc.skip_blank_lines()
            if sc.peek() == "]":
                sc.advance()
                return out
            value = self._parse_value()
            out.append(value)
            sc.skip_blank_lines()
            if sc.peek() == ",":
                sc.advance()
                continue
            sc.skip_blank_lines()
            sc.expect("]")
            return out

    def _parse_inline_table(self) -> _Table:
        sc = self.sc
        sc.expect("{")
        table = _Table(implicit=False)
        sc.skip_inline_ws()
        if sc.peek() == "}":
            sc.advance()
            self._finalize_inline(table)
            return table
        while True:
            path = self._parse_key_path(allow_dotted=True)
            sc.skip_ws()
            sc.expect("=")
            sc.skip_ws()
            value = self._parse_value()
            self._assign_inline(table, path, value)
            sc.skip_inline_ws()
            if sc.peek() == ",":
                sc.advance()
                sc.skip_inline_ws()
                if sc.peek() == "}":
                    raise sc.error("trailing comma in inline table")
                continue
            sc.expect("}")
            self._finalize_inline(table)
            return table

    def _finalize_inline(self, table: _Table) -> None:
        """Mark every table in an inline-table subtree as inline-sourced.

        Such tables are immutable: later headers and dotted keys must reject
        any attempt to extend them.
        """
        stack = [table]
        while stack:
            tbl = stack.pop()
            tbl.implicit = False
            tbl.dotted = False
            tbl.from_inline = True
            for v in tbl.values():
                if isinstance(v, _Table):
                    stack.append(v)

    def _assign_inline(self, table: _Table, path: tuple[str, ...], value) -> None:
        # Inline tables are immutable once created.  Intermediate tables
        # introduced by dotted keys inside the same inline table are implicit
        # and may be shared by sibling paths; any table already "filled in"
        # (explicit) or any scalar key may not be touched a second time.
        node = table
        for key in path[:-1]:
            sub = node.get(key)
            if sub is None:
                child = _Table(implicit=True)
                node[key] = child
                node = child
            elif isinstance(sub, _Table) and sub.implicit:
                node = sub
            else:
                raise self.sc.error(f"duplicate key {key!r} in inline table")
        leaf = path[-1]
        if leaf in node:
            raise self.sc.error(f"duplicate key {leaf!r} in inline table")
        node[leaf] = value


def _fmt(path: tuple[str, ...]) -> str:
    return ".".join(path)
