"""Exception types for the ztoml TOML 1.0 parser."""

from __future__ import annotations


class TOMLError(Exception):
    """Base class for every error raised while parsing TOML."""


class TOMLDecodeError(TOMLError, ValueError):
    """Raised when the input is not valid TOML 1.0.

    The message always carries a 1-based line/column location, formatted as
    ``"<message> (at line L, column C)"`` so callers can pinpoint the fault.
    """

    def __init__(self, message: str, line: int, col: int) -> None:
        self.line = line
        self.col = col
        super().__init__(f"{message} (at line {line}, column {col})")
