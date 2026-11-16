"""Recursive-descent parser implementing TOML 1.0 table semantics.

Table identity is tracked with three sets of dotted paths:

* ``defined``  — every table that has received an explicit header or an
  inline-table value; defining one twice is an error.
* ``closed``   — inline tables, which forbid later insertion of sub-keys.
* ``implicit`` — intermediate tables auto-created while walking dotted keys
  or headers; turning one into an explicit table later is an error.

Arrays-of-tables are handled in ``_define_aot``; the *last* element of every
array-of-tables on a header path becomes the current parent table.
"""

from __future__ import annotations

from ._errors import TOMLDecodeError
from ._lexer import Lexer, T, Token
from ._values import parse_integer, parse_scalar

_KEY_TOKENS = {T.BARE, T.BASIC, T.LITERAL}


class Parser:
    def __init__(self, text: str) -> None:
        self.tokens = Lexer(text).tokenize()
        self.i = 0
        self.root: dict = {}
        self.current: dict = self.root
        # dotted paths; integers inside a path mark an array-of-tables index
        self.defined: set[tuple] = set()
        self.closed: set[tuple] = set()
        self.implicit: set[tuple] = set()
        # implicit tables created specifically by dotted-key assignment;
        # these may never later receive an explicit [table] header, unlike
        # the intermediate tables a header path itself creates
        self.dotted_implicit: set[tuple] = set()
        self.inline: set[int] = set()   # id() of dicts produced as inline tables
        self.aot: set[int] = set()      # id() of lists that are arrays-of-tables
        self.current_path: tuple = ()

    # -- token helpers ------------------------------------------------------

    @property
    def tok(self) -> Token:
        return self.tokens[self.i]

    def advance(self) -> Token:
        t = self.tokens[self.i]
        self.i += 1
        return t

    def expect(self, kind: T, what: str) -> Token:
        if self.tok.kind is not kind:
            t = self.tok
            raise TOMLDecodeError(f"expected {what}, found {t.kind.name.lower()}", t.line, t.col)
        return self.advance()

    def skip_newlines(self) -> None:
        while self.tok.kind is T.NEWLINE:
            self.advance()

    # -- entry point --------------------------------------------------------

    def parse(self) -> dict:
        self.skip_newlines()
        while self.tok.kind is not T.EOF:
            if self.tok.kind is T.LBRACKET:
                self._parse_table_header()
            else:
                self._parse_keyval()
            self.skip_newlines()
        return self.root

    # -- keys ---------------------------------------------------------------

    def _parse_key(self) -> str:
        t = self.tok
        if t.kind not in _KEY_TOKENS:
            raise TOMLDecodeError("expected a key", t.line, t.col)
        self.advance()
        if t.kind is T.BARE:
            key = t.value
            if not key or not _is_bare_key(key):
                raise TOMLDecodeError(f"invalid bare key {key!r}", t.line, t.col)
            return key
        return t.value  # decoded string token

    def _parse_key_path(self) -> tuple[list[str], Token]:
        path = [self._parse_key()]
        first = self.tokens[self.i - 1]
        while self.tok.kind is T.DOT:
            self.advance()
            # dotted keys allow whitespace around dots but nothing else;
            # lexer already dropped spaces/tabs.
            path.append(self._parse_key())
        return path, first

    # -- key = value --------------------------------------------------------

    def _parse_keyval(self) -> None:
        path, key_tok = self._parse_key_path()
        self.expect(T.EQUALS, "'='")
        value = self._parse_value()
        if self.tok.kind not in (T.NEWLINE, T.EOF):
            raise TOMLDecodeError(
                "expected newline after key-value pair", self.tok.line, self.tok.col
            )
        self._assign_dotted(self.current, self.current_path, path, value, key_tok)

    def _assign_dotted(
        self,
        table: dict,
        base: tuple[str, ...],
        path: list[str],
        value: object,
        key_tok: Token,
    ) -> None:
        node = table
        node_path = base
        for depth, key in enumerate(path[:-1]):
            child_path = node_path + (key,)
            if child_path in self.closed:
                raise TOMLDecodeError(
                    f"cannot add key {key!r} to inline table", key_tok.line, key_tok.col
                )
            if key not in node:
                node[key] = {}
                self.implicit.add(child_path)
                self.dotted_implicit.add(child_path)
            elif not isinstance(node[key], dict):
                raise TOMLDecodeError(
                    f"cannot traverse non-table key {key!r}", key_tok.line, key_tok.col
                )
            node = node[key]
            node_path = child_path
        last = path[-1]
        last_path = node_path + (last,)
        if last in node:
            raise TOMLDecodeError(f"duplicate key {last!r}", key_tok.line, key_tok.col)
        if last_path in self.closed:
            raise TOMLDecodeError(
                f"cannot add key {last!r} to inline table", key_tok.line, key_tok.col
            )
        node[last] = value
        if isinstance(value, dict) and id(value) in self.inline:
            self._register_inline(value, last_path)

    def _register_inline(self, table: dict, path: tuple[str, ...]) -> None:
        """Mark every table reachable from an inline table as defined+closed."""
        self.defined.add(path)
        self.closed.add(path)
        for k, v in table.items():
            child = path + (k,)
            if isinstance(v, dict):
                self._register_inline(v, child)
            else:
                self.defined.add(child)

    # -- table headers ------------------------------------------------------

    def _parse_table_header(self) -> None:
        bracket = self.advance()  # first [
        is_aot = self.tok.kind is T.LBRACKET
        if is_aot:
            second = self.tok
            # [[ must be adjacent: no whitespace/newline between the brackets
            if second.line != bracket.line or second.col != bracket.col + 1:
                raise TOMLDecodeError(
                    "whitespace is not allowed inside array-of-tables brackets",
                    second.line, second.col,
                )
            self.advance()  # second [
        path, first_tok = self._parse_key_path()
        close1 = self.expect(T.RBRACKET, "']'")
        if is_aot:
            if self.tok.kind is not T.RBRACKET:
                raise TOMLDecodeError(
                    "expected closing ']]' for array-of-tables header",
                    self.tok.line, self.tok.col,
                )
            close2 = self.tok
            if close2.line != close1.line or close2.col != close1.col + 1:
                raise TOMLDecodeError(
                    "whitespace is not allowed inside array-of-tables brackets",
                    close2.line, close2.col,
                )
            self.advance()
        if self.tok.kind not in (T.NEWLINE, T.EOF):
            raise TOMLDecodeError(
                "expected newline after table header", self.tok.line, self.tok.col
            )
        if is_aot:
            self.current, self.current_path = self._define_aot(path, first_tok)
        else:
            self.current, self.current_path = self._define_table(path, first_tok, bracket)

    def _descend(
        self, node: dict, key: str, path: tuple, tok: Token
    ) -> tuple[object, tuple]:
        """Walk one header segment, creating implicit tables or entering
        the current element of an array-of-tables. Returns (node, path)."""
        # A closed inline table is immutable: no header may pass *through*
        # it to create or extend a sub-table. Terminal header checks alone
        # cannot catch descent through a closed ancestor to a new leaf.
        if path in self.closed:
            raise TOMLDecodeError(
                f"cannot extend closed inline table at {key!r}", tok.line, tok.col
            )
        if key not in node:
            node[key] = {}
            self.implicit.add(path)
            return node[key], path
        existing = node[key]
        if isinstance(existing, list):
            if id(existing) not in self.aot:
                raise TOMLDecodeError(
                    f"cannot traverse plain array key {key!r}", tok.line, tok.col
                )
            if not existing:
                raise TOMLDecodeError(
                    f"cannot append to empty array-of-tables {key!r}", tok.line, tok.col
                )
            element = existing[-1]
            return element, path + (len(existing) - 1,)
        if not isinstance(existing, dict):
            raise TOMLDecodeError(
                f"cannot define table over non-table key {key!r}", tok.line, tok.col
            )
        return existing, path

    def _define_table(
        self, path: list[str], tok: Token, bracket: Token
    ) -> tuple[dict, tuple]:
        node = self.root
        node_path: tuple = ()
        for key in path:
            node_path = node_path + (key,)
            node, node_path = self._descend(node, key, node_path, tok)

        full = node_path
        if full in self.defined:
            raise TOMLDecodeError(f"table {'.'.join(path)!r} defined more than once",
                                  tok.line, tok.col)
        if full in self.closed:
            raise TOMLDecodeError(f"cannot extend inline table {'.'.join(path)!r}",
                                  tok.line, tok.col)
        if full in self.dotted_implicit:
            raise TOMLDecodeError(
                f"cannot define table {'.'.join(path)!r}: it was already created "
                "implicitly by a dotted key", tok.line, tok.col
            )
        self.defined.add(full)
        self.implicit.discard(full)
        return node, full

    def _define_aot(
        self, path: list[str], tok: Token
    ) -> tuple[dict, tuple]:
        node = self.root
        node_path: tuple = ()
        for key in path[:-1]:
            node_path = node_path + (key,)
            node, node_path = self._descend(node, key, node_path, tok)

        last = path[-1]
        leaf_path = node_path + (last,)
        created = last not in node
        if created:
            node[last] = []
        arr = node[last]
        if not isinstance(arr, list):
            raise TOMLDecodeError(
                f"key {last!r} already exists and is not an array-of-tables",
                tok.line, tok.col,
            )
        # an existing plain array (even an empty one) can never become an AOT
        if not created and id(arr) not in self.aot:
            if leaf_path in self.defined or leaf_path in self.closed:
                raise TOMLDecodeError(
                    f"cannot append array-of-tables over existing key {last!r}",
                    tok.line, tok.col,
                )
            raise TOMLDecodeError(
                f"key {last!r} already exists and is not an array-of-tables",
                tok.line, tok.col,
            )
        new_table: dict = {}
        arr.append(new_table)
        self.aot.add(id(arr))
        marker_path = leaf_path + (len(arr) - 1,)
        self.defined.add(marker_path)
        self.implicit.discard(leaf_path)
        return new_table, marker_path

    # -- values -------------------------------------------------------------

    def _parse_value(self) -> object:
        t = self.tok
        if t.kind is T.BARE:
            raw = t.value
            self.advance()
            # The lexer already merged a legal single-space datetime into
            # one BARE token. '.' is both a key separator and a decimal
            # point; in value position an *adjacent* BARE '.' BARE sequence
            # belongs to one scalar (floats, times with fractions). A space
            # before the dot (e.g. "0 .1") is invalid, so adjacency of token
            # positions is required. Key parsing never reaches here.
            while (
                self.tok.kind is T.DOT
                and self._peek_kind(1) is T.BARE
                and self._dot_adjacent(t, self.tok)
            ):
                dot = self.advance()  # '.'
                nxt = self.advance()
                raw += "." + nxt.value
                t = nxt  # chain adjacency for further dots
            return parse_scalar(raw)
        if t.kind in (T.BASIC, T.LITERAL, T.MBASIC, T.MLITERAL):
            self.advance()
            return t.value
        if t.kind is T.LBRACKET:
            return self._parse_array()
        if t.kind is T.LBRACE:
            return self._parse_inline_table()
        raise TOMLDecodeError("expected a value", t.line, t.col)

    def _peek_kind(self, off: int) -> T:
        idx = self.i + off
        if idx < len(self.tokens):
            return self.tokens[idx].kind
        return T.EOF

    def _dot_adjacent(self, prev: Token, dot: Token) -> bool:
        """True when ``prev``, ``.`` and the following BARE touch with no
        whitespace, so they form one scalar rather than a key separator."""
        idx_next = self.i + 1
        if idx_next >= len(self.tokens):
            return False
        nxt = self.tokens[idx_next]
        prev_end = prev.col + len(prev.value)
        dot_touches_prev = dot.line == prev.line and dot.col == prev_end
        next_touches_dot = (
            nxt.kind is T.BARE and nxt.line == dot.line and nxt.col == dot.col + 1
        )
        return dot_touches_prev and next_touches_dot

    def _parse_array(self) -> list:
        self.expect(T.LBRACKET, "'['")
        result: list = []
        self.skip_newlines()
        if self.tok.kind is T.RBRACKET:
            self.advance()
            return result
        while True:
            self.skip_newlines()
            result.append(self._parse_value())
            self.skip_newlines()
            if self.tok.kind is T.COMMA:
                self.advance()
                self.skip_newlines()
                if self.tok.kind is T.RBRACKET:
                    self.advance()
                    return result
                continue
            if self.tok.kind is T.RBRACKET:
                self.advance()
                return result
            raise TOMLDecodeError(
                "expected ',' or ']' in array", self.tok.line, self.tok.col
            )

    def _parse_inline_table(self) -> dict:
        self.expect(T.LBRACE, "'{'")
        table: dict = {}
        self.inline.add(id(table))
        if self.tok.kind is T.RBRACE:
            self.advance()
            return table
        # Track full key-path tuples. A quoted key containing a dot ('"a.b"')
        # is one key and stays distinct from a two-segment path (a.b). Two
        # key paths conflict only when they are equal or one is a prefix of
        # the other (a value/table collision); same-named leaves in different
        # subtrees, e.g. {a.x=1, b.x=2}, are legal.
        seen_paths: list[tuple[str, ...]] = []
        while True:
            if self.tok.kind is T.NEWLINE:
                raise TOMLDecodeError(
                    "inline table must not contain a raw newline",
                    self.tok.line, self.tok.col,
                )
            path, key_tok = self._parse_key_path()
            path_tup = tuple(path)
            if any(_paths_conflict(path_tup, prev) for prev in seen_paths):
                raise TOMLDecodeError(
                    f"duplicate key {'.'.join(path)!r} in inline table",
                    key_tok.line, key_tok.col,
                )
            seen_paths.append(path_tup)
            self.expect(T.EQUALS, "'='")
            value = self._parse_value()
            self._assign_inline_dotted(table, path, value, key_tok)
            if self.tok.kind is T.COMMA:
                self.advance()
                if self.tok.kind is T.RBRACE:
                    raise TOMLDecodeError(
                        "trailing comma not allowed in inline table",
                        self.tok.line, self.tok.col,
                    )
                continue
            break
        self.expect(T.RBRACE, "'}'")
        return table

    def _assign_inline_dotted(
        self, table: dict, path: list[str], value: object, key_tok: Token
    ) -> None:
        node = table
        for key in path[:-1]:
            existing = node.get(key)
            if existing is None:
                existing = {}
                node[key] = existing
            elif not isinstance(existing, dict):
                raise TOMLDecodeError(
                    f"cannot traverse non-table key {key!r}", key_tok.line, key_tok.col
                )
            node = existing
        last = path[-1]
        if last in node:
            raise TOMLDecodeError(f"duplicate key {last!r}", key_tok.line, key_tok.col)
        node[last] = value


def _is_bare_key(text: str) -> bool:
    # TOML 1.0: bare keys contain only ASCII letters/digits, '_' and '-'.
    for ch in text:
        if not (ch.isascii() and (ch.isalnum() or ch in "_-")):
            return False
    return True


def _paths_conflict(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """Two dotted key paths conflict when equal or one prefixes the other:
    that makes one a table where the other expects a scalar (or a duplicate)."""
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return longer[: len(shorter)] == shorter


def loads(text: str) -> dict:
    if not isinstance(text, str):
        raise TypeError("tomlone.loads expects a str")
    # A leading UTF-8 BOM is allowed and stripped. BOM appearing anywhere at
    # a *lexical* position is rejected by the lexer; a BOM inside a string is
    # legal content and is consumed by the string tokenizer, so we must not
    # blanket-scan the whole document here.
    if text.startswith("﻿"):
        text = text[1:]
    for ch in text:
        if ord(ch) == 0:
            raise TOMLDecodeError("null byte in document")
    return Parser(text).parse()


def load(fp) -> dict:
    data = fp.read()
    if isinstance(data, bytes):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise TOMLDecodeError(f"document is not valid UTF-8: {exc}") from exc
    else:
        text = data
        if text.startswith("﻿"):
            text = text[1:]
    return loads(text)
