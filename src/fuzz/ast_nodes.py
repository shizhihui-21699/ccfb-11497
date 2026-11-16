"""AST node definitions for the grammar-aware generator and mutators.

The generator builds documents as :class:`Document` objects and serializes
them; mutators can operate either on the AST (structure-aware) or directly
on rendered text (byte-level). Keeping both levels available is what lets
the fuzzer reach semantic table rules *and* lexer edge cases.
"""

from __future__ import annotations

import dataclasses
from typing import Union


@dataclasses.dataclass
class KeyVal:
    key_path: list[str]          # dotted key components, already unquoted
    value: object                # scalar / list / InlineTable
    quoted_keys: bool = False    # render keys as basic strings
    comment: str | None = None


@dataclasses.dataclass
class TableHeader:
    path: list[str]
    array: bool = False
    quoted: bool = False
    comment: str | None = None


@dataclasses.dataclass
class InlineTable:
    items: list                  # list[KeyVal]
    dotted: bool = False


# statements are either KeyVal or TableHeader
Statement = Union[KeyVal, TableHeader]


@dataclasses.dataclass
class Document:
    statements: list             # list[Statement]

    def render(self) -> str:
        from .render import render_document
        return render_document(self)
