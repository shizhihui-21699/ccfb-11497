"""Grammar-aware generator producing *valid* TOML 1.0 documents.

Strategy: build a set of table blocks (root plus generated [table] and
[[array-of-tables]] headers), fill each with key-values whose values come
from a weighted recursive value generator. Because the AST is assembled
through the same structural rules TOML imposes, rendered output is valid;
the mutators are the component that breaks validity in controlled ways.
"""

from __future__ import annotations

import random
import string

from .ast_nodes import Document, InlineTable, KeyVal, TableHeader
from .render import _Raw

_BARE_CHARS = string.ascii_letters + string.digits + "_-"


class Generator:
    def __init__(self, rng: random.Random, max_depth: int = 3) -> None:
        self.rng = rng
        self.max_depth = max_depth
        self._key_counter = 0
        # global dotted-path registry: every key path actually written into
        # the document is tracked here so root keys, table headers, dotted
        # keys and inline-table keys can never collide
        self._used_paths: set[tuple[str, ...]] = set()
        self._table_paths: set[tuple[str, ...]] = set()

    # -- keys ---------------------------------------------------------------

    def bare_key(self) -> str:
        self._key_counter += 1
        length = self.rng.randint(1, 10)
        body = "".join(self.rng.choice(_BARE_CHARS) for _ in range(length))
        if body[0].isdigit():
            body = "k" + body
        return f"{body}_{self._key_counter}"

    def quoted_key(self) -> str:
        # every generated quoted key gets a unique suffix so empty/duplicate
        # keys can never occur
        self._key_counter += 1
        base = self.rng.choice([
            "key with space", "key.with.dot", 'key"quote',
            "key#hash", "empty-", "unicode-é",
        ])
        return f"{base}{self._key_counter}"

    def key_path(self) -> tuple[list[str], bool]:
        quoted = self.rng.random() < 0.18
        n = 1 if self.rng.random() < 0.7 else self.rng.randint(2, 3)
        if quoted:
            return [self.quoted_key() for _ in range(n)], True
        return [self.bare_key() for _ in range(n)], False

    def unique_path(self) -> tuple[list[str], bool]:
        """Return a globally unused dotted key path."""
        for _ in range(20):
            path, quoted = self.key_path()
            tup = tuple(path)
            if tup not in self._used_paths:
                self._used_paths.add(tup)
                return path, quoted
        path = [self.bare_key()]
        self._used_paths.add(tuple(path))
        return path, False

    # -- scalar values ------------------------------------------------------

    def scalar(self, depth: int) -> object:
        kind = self.rng.choices(
            population=[
                "integer", "float", "bool", "string", "literal",
                "multiline", "datetime", "array", "inline",
            ],
            weights=[22, 18, 8, 18, 6, 6, 10, 7, 5],
            k=1,
        )[0]
        if depth >= self.max_depth:
            kind = self.rng.choice(["integer", "float", "bool", "string", "datetime"])
        dispatch = {
            "integer": self._integer,
            "float": self._float,
            "bool": lambda: self.rng.choice([True, False]),
            "string": self._basic_string,
            "literal": self._literal_string,
            "multiline": self._multiline_string,
            "datetime": self._datetime,
            "array": lambda: self._array(depth + 1),
            "inline": lambda: self._inline(depth + 1),
        }
        return dispatch[kind]()

    def _integer(self) -> _Raw:
        style = self.rng.choices(
            ["decimal", "hex", "oct", "bin", "underscored"],
            weights=[40, 15, 10, 10, 25], k=1,
        )[0]
        if style == "decimal":
            return _Raw(str(self.rng.randint(-(2 ** 53), 2 ** 53)))
        value = self.rng.randint(0, 2 ** 40)
        if style == "hex":
            return _Raw("0x" + self._underscore(f"{value:X}", 4))
        if style == "oct":
            return _Raw("0o" + self._underscore(format(value, "o"), 3))
        if style == "bin":
            return _Raw("0b" + self._underscore(format(value, "b"), 4))
        plain = str(self.rng.randint(1, 2 ** 40))
        return _Raw(self._underscore(plain, 3))

    @staticmethod
    def _underscore(text: str, group: int) -> str:
        if len(text) <= group:
            return text
        chunks = [text[max(0, i - group):i]
                  for i in range(len(text), 0, -group)][::-1]
        return "_".join(chunks)

    def _float(self) -> _Raw:
        style = self.rng.choices(
            ["plain", "exponent", "underscored", "special"],
            weights=[45, 30, 15, 10], k=1,
        )[0]
        if style == "special":
            return _Raw(self.rng.choice(["inf", "-inf", "+inf", "nan", "+nan", "-nan"]))
        whole = self.rng.randint(0, 10 ** 9)
        frac = self.rng.randint(1, 10 ** 6)
        if style == "plain":
            sign = "-" if self.rng.random() < 0.3 else ""
            return _Raw(f"{sign}{whole}.{frac}")
        if style == "exponent":
            exp = self.rng.randint(-20, 20)
            esign = "+" if exp >= 0 and self.rng.random() < 0.5 else ""
            return _Raw(f"{whole}.{frac}e{esign}{exp}")
        return _Raw(f"{self._underscore(str(whole), 3)}.{self._underscore(str(frac), 3)}")

    def _basic_string(self) -> str:
        return self.rng.choice([
            "hello", "", "tab\there", "quote\"inside", "back\\slash",
            "unicode αβγ", "line one line two", "emoji 🎉",
            "control\x08\x0c", "mixed 'single' \"double\"",
        ])

    def _literal_string(self) -> _Raw:
        body = self.rng.choice([
            "C:\\Users\\admin", 'literal "quotes"', "empty-lit",
            "path/to/file", "no escapes \\n \\t",
        ])
        # single-quoted strings must not contain a real newline
        return _Raw("'" + body + "'")

    def _multiline_string(self) -> _Raw:
        body = self.rng.choice([
            "line one\nline two\nline three",
            'triple "" inside',
            "backslash \\\n  trimmed continuation",
            "single ' and double \" mixed",
        ])
        quote = self.rng.choice(['"""', "'''"])
        # never end content with the same quote char, which would collide
        # with the closing delimiter
        return _Raw(quote + "\n" + body + "\n" + quote)

    def _datetime(self) -> _Raw:
        y = self.rng.randint(1900, 2100)
        mo = self.rng.randint(1, 12)
        d = self.rng.randint(1, 28)
        hh = self.rng.randint(0, 23)
        mm = self.rng.randint(0, 59)
        ss = self.rng.randint(0, 59)
        frac = self.rng.choice([None, f".{self.rng.randint(1, 999999999)}"])
        kind = self.rng.choice(
            ["offset", "local-dt", "local-date", "local-time", "space-sep"],
        )
        base = f"{y:04d}-{mo:02d}-{d:02d}"
        time = f"{hh:02d}:{mm:02d}:{ss:02d}" + (frac or "")
        if kind == "local-date":
            return _Raw(base)
        if kind == "local-time":
            return _Raw(time)
        if kind == "local-dt":
            return _Raw(f"{base}T{time}")
        if kind == "space-sep":
            offset = self._offset()
            return _Raw(f"{base} {time}{offset}")
        return _Raw(f"{base}T{time}{self._offset()}")

    def _offset(self) -> str:
        return self.rng.choice(["Z", "+00:00", "-05:00", "+08:00", "+13:00", "+05:30"])

    # -- containers ---------------------------------------------------------

    def _array(self, depth: int) -> list:
        n = self.rng.randint(0, 6)
        if n == 0:
            return []
        # mostly homogeneous, occasionally mixed scalar kinds
        if self.rng.random() < 0.8:
            kind = self.rng.choice(["integer", "float", "string", "bool", "datetime"])
            values = []
            for _ in range(n):
                if kind == "integer":
                    values.append(self._integer())
                elif kind == "float":
                    values.append(self._float())
                elif kind == "string":
                    values.append(self._basic_string())
                elif kind == "bool":
                    values.append(self.rng.choice([True, False]))
                else:
                    values.append(self._datetime())
            return values
        return [self.scalar(depth) for _ in range(n)]

    def _inline(self, depth: int) -> InlineTable:
        n = self.rng.randint(1, 4)
        items = []
        dotted = self.rng.random() < 0.3
        used: set[tuple[str, ...]] = set()
        for _ in range(n):
            path, quoted = self.key_path()
            tup = tuple(path)
            if tup in used:
                continue
            used.add(tup)
            items.append(KeyVal(path, self.scalar(depth), quoted_keys=quoted))
        if not items:
            path, quoted = self.key_path()
            items.append(KeyVal(path, self.scalar(depth), quoted_keys=quoted))
        return InlineTable(items, dotted=dotted)

    # -- documents ----------------------------------------------------------

    def document(self, num_tables: int = 4) -> Document:
        statements: list = []
        # root-level key-values (paths globally unique)
        for _ in range(self.rng.randint(2, 6)):
            statements.append(self._keyval())

        for _ in range(num_tables):
            path, quoted = self._unique_table_path()
            array = self.rng.random() < 0.35
            statements.append(TableHeader(path, array=array, quoted=quoted))
            # keys inside this table only need to be unique relative to the
            # table; use a scoped registry to keep them collision-free
            table_keys: set[tuple[str, ...]] = set()
            for _ in range(self.rng.randint(1, 5)):
                path2, quoted2 = self._scoped_path(table_keys)
                statements.append(
                    KeyVal(path2, self.scalar(0), quoted_keys=quoted2)
                )
        return Document(statements)

    def _unique_table_path(self) -> tuple[list[str], bool]:
        for _ in range(20):
            path, quoted = self.key_path()
            tup = tuple(path)
            if tup not in self._table_paths and tup not in self._used_paths:
                self._table_paths.add(tup)
                self._used_paths.add(tup)
                return path, quoted
        path = [self.bare_key()]
        self._table_paths.add(tuple(path))
        self._used_paths.add(tuple(path))
        return path, False

    def _scoped_path(self, scope: set[tuple[str, ...]]) -> tuple[list[str], bool]:
        for _ in range(20):
            path, quoted = self.key_path()
            tup = tuple(path)
            if tup not in scope:
                scope.add(tup)
                return path, quoted
        path = [self.bare_key()]
        scope.add(tuple(path))
        return path, False

    def _keyval(self) -> KeyVal:
        path, quoted = self.unique_path()
        return KeyVal(path, self.scalar(0), quoted_keys=quoted,
                      comment=None)


def generate(rng: random.Random, **kwargs) -> str:
    return Generator(rng, **kwargs).document().render()
