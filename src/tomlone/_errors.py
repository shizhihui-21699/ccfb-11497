"""Exception type shared by every parsing stage."""

from __future__ import annotations


class TOMLDecodeError(ValueError):
    """Raised when input is not valid TOML 1.0.

    Subclasses :class:`ValueError` to match the ``tomllib`` convention.
    """

    def __init__(self, message: str, line: int | None = None, col: int | None = None) -> None:
        self.line = line
        self.col = col
        if line is not None:
            message = f"{message} (at line {line}, column {col})"
        super().__init__(message)
