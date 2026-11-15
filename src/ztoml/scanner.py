"""Character-level scanner for TOML 1.0 source text.

The scanner owns source position bookkeeping (line/column tracking) and the
low-level lexical building blocks (whitespace, comments, keys, strings,
numbers, booleans).  Higher-level grammar structure (tables, arrays, inline
tables, key/value pairs) is assembled in :mod:`ztoml.parser`.
"""

from __future__ import annotations

from .errors import TOMLDecodeError

# Control characters that may never appear unescaped inside a basic string
# (U+0000..U+001F except U+0009 tab, plus U+007F DEL).
_FORBIDDEN_BASIC = {c for c in range(0x00, 0x20) if c != 0x09} | {0x7F}

_BARE_KEY_CHARS = set(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    "_-"
)

# UTF-8 byte-order mark; legal only as the very first character of a file.
BOM = "\ufeff"


class Scanner:
    def __init__(self, text: str) -> None:
        if not text:
            text = ""
        # TOML is always UTF-8; reject surrogate code points produced by
        # surrogatepass-style decoding.
        if any(0xD800 <= ord(c) <= 0xDFFF for c in text):
            # Location is not meaningful here.
            raise TOMLDecodeError("source contains surrogate code point", 1, 1)
        self.src = text
        self.n = len(text)
        self.pos = 0
        self.line = 1
        self.col = 1

    # ------------------------------------------------------------------ utils
    def eof(self) -> bool:
        return self.pos >= self.n

    def peek(self, off: int = 0) -> str:
        p = self.pos + off
        return self.src[p] if p < self.n else ""

    def advance(self) -> str:
        c = self.src[self.pos]
        self.pos += 1
        if c == "\n":
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        # TOML 1.0: control characters other than tab are not permitted
        # anywhere in the document (comment text included), nor is DEL.
        # CR is allowed only as the first half of CRLF; pairing is checked by
        # consume_eol and the multiline string readers.
        code = ord(c)
        if (code < 0x20 and code not in (0x09, 0x0A, 0x0D)) or code == 0x7F:
            raise self.error(
                f"control character U+{code:04X} is not permitted"
            )
        return c

    def error(self, message: str) -> TOMLDecodeError:
        return TOMLDecodeError(message, self.line, self.col)

    def starts(self, s: str) -> bool:
        return self.src.startswith(s, self.pos)

    def expect(self, c: str) -> None:
        if self.peek() != c:
            raise self.error(f"expected {c!r}")
        self.advance()

    # ------------------------------------------------------------- whitespace
    def skip_ws(self) -> None:
        """Skip spaces and tabs (horizontal whitespace only)."""
        while self.peek() in (" ", "\t"):
            self.advance()

    def skip_inline_ws(self) -> None:
        """Skip spaces, tabs and the (single-line) comments that follow them."""
        while True:
            self.skip_ws()
            if self.peek() == "#":
                while not self.eof() and self.peek() not in ("\n", "\r"):
                    self.advance()
            else:
                return

    def consume_eol(self) -> None:
        """Consume exactly one line ending (LF or CRLF); error on anything else."""
        c = self.peek()
        if c == "\n":
            self.advance()
        elif c == "\r":
            self.advance()
            if self.peek() != "\n":
                # A bare CR is never a valid TOML line ending.
                raise self.error("bare carriage return; line endings must be LF or CRLF")
            self.advance()
        else:
            raise self.error("expected newline")

    def skip_blank_lines(self) -> None:
        """Consume any mix of blank/comment-only lines and whitespace."""
        while True:
            self.skip_ws()
            if self.peek() == "#":
                while not self.eof() and self.peek() not in ("\n", "\r"):
                    self.advance()
            if self.peek() in ("\n", "\r"):
                self.consume_eol()
                continue
            return

    # ------------------------------------------------------------------- keys
    def parse_key(self) -> str:
        c = self.peek()
        if c in _BARE_KEY_CHARS:
            start = self.pos
            while self.peek() in _BARE_KEY_CHARS:
                self.advance()
            return self.src[start:self.pos]
        if c == '"':
            if self.starts('"""'):
                raise self.error("multiline strings are not allowed as keys")
            return self.parse_basic_string()
        if c == "'":
            if self.starts("'''"):
                raise self.error("multiline strings are not allowed as keys")
            return self.parse_literal_string()
        if self.eof():
            raise self.error("expected key, found end of input")
        raise self.error(f"invalid key character {c!r}")

    # --------------------------------------------------------------- strings
    def parse_literal_string(self) -> str:
        if self.starts("'''"):
            return self._parse_multiline_literal()
        self.expect("'")
        out: list[str] = []
        while True:
            c = self.peek()
            if c == "":
                raise self.error("unterminated literal string")
            if c == "'":
                self.advance()
                return "".join(out)
            if c == "\n" or c == "\r":
                raise self.error("newline in single-line literal string")
            # Tab is the only control character legal inside a string.
            if c != "\t" and (ord(c) < 0x20 or ord(c) == 0x7F):
                raise self.error("control character in literal string")
            out.append(self.advance())

    def parse_basic_string(self) -> str:
        if self.starts('"""'):
            return self._parse_multiline_basic()
        self.expect('"')
        out: list[str] = []
        while True:
            c = self.peek()
            if c == "":
                raise self.error("unterminated basic string")
            if c == '"':
                self.advance()
                return "".join(out)
            if c == "\n" or c == "\r":
                raise self.error("newline in single-line basic string")
            if c == "\\":
                out.append(self._parse_escape())
                continue
            if ord(c) in _FORBIDDEN_BASIC:
                raise self.error("unescaped control character in basic string")
            out.append(self.advance())

    def _trim_multiline_newline(self) -> None:
        """Consume the newline immediately following a ``\"\"\"`` / ``'''`` opener.

        Only LF or CRLF are legal here; a bare CR must be rejected just like
        anywhere else in a multiline string.
        """
        if self.peek() == "\n":
            self.advance()
        elif self.peek() == "\r":
            self.advance()
            if self.peek() != "\n":
                raise self.error("bare carriage return in multiline string")
            self.advance()

    def _parse_multiline_literal(self) -> str:
        self.pos += 3
        self.col += 3
        self._trim_multiline_newline()
        out: list[str] = []
        while True:
            if self.eof():
                raise self.error("unterminated multiline literal string")
            # Handle a run of apostrophes: up to two are content, three close
            # the string; four or five are one/two content quotes + closer.
            if self.peek() == "'":
                run = self._count_and_consume("'")
                if run < 3:
                    out.append("'" * run)
                    continue
                if run == 3:
                    return "".join(out)
                if run <= 5:
                    out.append("'" * (run - 3))
                    return "".join(out)
                raise self.error("more than five consecutive quotes in string")
            c = self.peek()
            if c == "\r":
                # CRLF normalizes to LF; a bare CR is rejected.
                self.advance()
                if self.peek() != "\n":
                    raise self.error("bare carriage return in multiline string")
                self.advance()
                out.append("\n")
                continue
            if c != "\n" and c != "\t" and (ord(c) < 0x20 or ord(c) == 0x7F):
                raise self.error("control character in multiline literal string")
            out.append(self.advance())

    def _parse_multiline_basic(self) -> str:
        self.pos += 3
        self.col += 3
        self._trim_multiline_newline()
        out: list[str] = []
        while True:
            if self.eof():
                raise self.error("unterminated multiline basic string")
            if self.peek() == '"':
                run = self._count_and_consume('"')
                if run < 3:
                    out.append('"' * run)
                    continue
                if run == 3:
                    return "".join(out)
                if run <= 5:
                    out.append('"' * (run - 3))
                    return "".join(out)
                raise self.error("more than five consecutive quotes in string")
            c = self.peek()
            if c == "\\":
                nxt = self.peek(1)
                if nxt in (" ", "\t", "\n", "\r"):
                    self._parse_line_ending_backslash()
                    continue
                out.append(self._parse_escape())
                continue
            if c == "\r":
                self.advance()
                if self.peek() != "\n":
                    raise self.error("bare carriage return in multiline string")
                self.advance()
                out.append("\n")
                continue
            if c != "\n" and c != "\t" and ord(c) in _FORBIDDEN_BASIC:
                raise self.error(
                    "unescaped control character in multiline basic string"
                )
            out.append(self.advance())

    def _count_and_consume(self, quote: str) -> int:
        """Consume and return the length of a run of ``quote`` (max 6)."""
        run = 0
        while run < 6 and self.peek() == quote:
            self.advance()
            run += 1
        return run

    def _parse_line_ending_backslash(self) -> None:
        """Handle ``\\`` line continuation.

        Valid shape: backslash, optional horizontal whitespace, then one or
        more blank lines containing only whitespace.  A backslash followed by
        just spaces/tabs (no newline) is an invalid escape.
        """
        self.advance()  # backslash
        while self.peek() in (" ", "\t"):
            self.advance()
        saw_newline = False
        while self.peek() in ("\n", "\r"):
            self.consume_eol()
            saw_newline = True
            while self.peek() in (" ", "\t"):
                self.advance()
        if not saw_newline:
            raise self.error(
                "line-ending backslash must be followed by a newline"
            )

    def _parse_escape(self) -> str:
        self.expect("\\")
        c = self.peek()
        simple = {
            "b": "\b",
            "t": "\t",
            "n": "\n",
            "f": "\f",
            "r": "\r",
            '"': '"',
            "\\": "\\",
        }
        if c in simple:
            self.advance()
            return simple[c]
        if c == "u":
            self.advance()
            return self._parse_unicode_hex(4)
        if c == "U":
            self.advance()
            return self._parse_unicode_hex(8)
        raise self.error(f"invalid escape sequence \\{c}")

    def _parse_unicode_hex(self, digits: int) -> str:
        hex_digits = ""
        for _ in range(digits):
            c = self.peek()
            if c not in "0123456789abcdefABCDEF":
                raise self.error(f"expected {digits} hex digits in unicode escape")
            hex_digits += c
            self.advance()
        cp = int(hex_digits, 16)
        if 0xD800 <= cp <= 0xDFFF or cp > 0x10FFFF:
            raise self.error("unicode escape is not a valid scalar value")
        return chr(cp)
