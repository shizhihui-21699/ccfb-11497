"""Lexer for TOML 1.0.

The lexer is deliberately dumb about numbers, booleans and datetimes: bare
runs of non-structural characters are emitted as ``BARE`` tokens and the
parser classifies them. Strings are fully decoded here (escapes, multiline
trimming, CRLF normalisation) because quote handling is lexical by nature.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from ._errors import TOMLDecodeError


class T(Enum):
    BARE = auto()        # unquoted run; parser decides int/float/bool/date/...
    BASIC = auto()       # "..."       -> decoded str
    LITERAL = auto()     # '...'       -> raw str
    MBASIC = auto()      # \"\"\"...\"\"\" -> decoded str
    MLITERAL = auto()    # '''...'''   -> raw str
    LBRACKET = auto()
    RBRACKET = auto()
    LBRACE = auto()
    RBRACE = auto()
    COMMA = auto()
    EQUALS = auto()
    DOT = auto()
    NEWLINE = auto()
    EOF = auto()


_STRUCTURAL = set("[]{}=,.")


def _date_shaped(text: str) -> bool:
    return len(text) >= 10 and text[:4].isdigit() and text[4] == "-"


@dataclass(frozen=True)
class Token:
    kind: T
    value: object
    line: int
    col: int
    raw: str = ""


class Scanner:
    """Character stream with 1-based position tracking and helpers."""

    __slots__ = ("text", "pos", "line", "col", "n")

    def __init__(self, text: str) -> None:
        self.text = text
        self.n = len(text)
        self.pos = 0
        self.line = 1
        self.col = 1

    def eof(self) -> bool:
        return self.pos >= self.n

    def peek(self, off: int = 0) -> str:
        p = self.pos + off
        return self.text[p] if p < self.n else ""

    def advance(self) -> str:
        ch = self.text[self.pos]
        self.pos += 1
        if ch == "\n":
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return ch

    def starts(self, literal: str) -> bool:
        return self.text.startswith(literal, self.pos)

    def err(self, msg: str, line: int | None = None, col: int | None = None) -> TOMLDecodeError:
        return TOMLDecodeError(
            msg, line if line is not None else self.line, col if col is not None else self.col
        )


class Lexer:
    def __init__(self, text: str) -> None:
        self.s = Scanner(text)
        self.tokens: list[Token] = []

    # -- entry point -------------------------------------------------------

    def tokenize(self) -> list[Token]:
        s = self.s
        while not s.eof():
            ch = s.peek()
            if ch == "﻿":
                # A BOM is only legal as the document's leading character
                # (already stripped by loads) or as content inside a string
                # (consumed by the string tokenizers, so never reaches here).
                raise s.err("BOM is only allowed at the start of the document")
            if ch == "\n":
                self._emit_newline()
            elif ch in " \t":
                s.advance()
            elif ch == "#":
                self._skip_comment()
            elif ch == "\r":
                if s.peek(1) != "\n":
                    raise s.err("bare carriage return must be part of CRLF")
                s.advance()  # \r
                s.advance()  # \n
                self.tokens.append(Token(T.NEWLINE, None, s.line - 1, 1, "\\r\\n"))
            elif ch == '"':
                self._lex_string(multiline=False)
            elif ch == "'":
                self._lex_string(multiline=True)
            elif ch in _STRUCTURAL:
                self._lex_structural(ch)
            else:
                self._lex_bare()
        self.tokens.append(Token(T.EOF, None, s.line, s.col, ""))
        self._collapse_blank_lines()
        return self.tokens

    # -- whitespace / comments --------------------------------------------

    def _emit_newline(self) -> None:
        s = self.s
        line, col = s.line, s.col
        s.advance()
        self.tokens.append(Token(T.NEWLINE, None, line, col, "\n"))

    def _skip_comment(self) -> None:
        s = self.s
        while not s.eof() and s.peek() not in "\n\r":
            ch = s.advance()
            code = ord(ch)
            # tab is allowed inside comments; every other C0 control and DEL
            # (0x7f, which sits inside the nominal printable range) is forbidden
            if ch == "\t" or (0x20 <= code <= 0x10FFFF and code != 0x7F):
                continue
            raise s.err("control character in comment")

    def _collapse_blank_lines(self) -> None:
        out: list[Token] = []
        for tok in self.tokens:
            if tok.kind is T.NEWLINE and out and out[-1].kind is T.NEWLINE:
                continue
            out.append(tok)
        self.tokens = out

    # -- structural punctuation --------------------------------------------

    def _lex_structural(self, ch: str) -> None:
        s = self.s
        line, col = s.line, s.col
        s.advance()
        kind = {
            "[": T.LBRACKET,
            "]": T.RBRACKET,
            "{": T.LBRACE,
            "}": T.RBRACE,
            ",": T.COMMA,
            "=": T.EQUALS,
            ".": T.DOT,
        }[ch]
        self.tokens.append(Token(kind, ch, line, col, ch))

    # -- bare runs ----------------------------------------------------------

    def _lex_bare(self) -> None:
        s = self.s
        line, col = s.line, s.col
        start = s.pos
        while not s.eof():
            ch = s.peek()
            if ch in " \t\n\r#" or ch in _STRUCTURAL:
                break
            code = ord(ch)
            if code < 0x20 or code == 0x7F:
                raise s.err("bare control character outside string")
            s.advance()
        raw = s.text[start:s.pos]
        if not self._try_merge_space_datetime(raw, line, col):
            self.tokens.append(Token(T.BARE, raw, line, col, raw))

    def _try_merge_space_datetime(self, date_raw: str, line: int, col: int) -> bool:
        """Merge ``YYYY-MM-DD SPACE HH:MM...`` into one BARE token.

        The space separator for a space-delimited datetime must be exactly
        one U+0020. Because this runs while whitespace is still visible, a
        double space / tab is left unmerged and later rejected, closing the
        'multiple spaces bypass' gap. Returns True when it merged.
        """
        if not _date_shaped(date_raw):
            return False
        s = self.s
        if s.peek() != " " or s.peek(1) in (" ", "\t"):
            return False
        n = s.n
        q = s.pos + 1
        if q >= n or not s.text[q].isdigit():
            return False
        r = q
        while r < n:
            ch = s.text[r]
            if ch in " \t\n\r#" or ch in _STRUCTURAL:
                break
            r += 1
        time_raw = s.text[q:r]
        # require a real time start HH:MM so we never swallow a later key
        if len(time_raw) < 5 or time_raw[2] != ":" or not time_raw[:2].isdigit():
            return False
        while s.pos < r:  # commit: consume the single space + time token
            s.advance()
        merged = date_raw + " " + time_raw
        self.tokens.append(Token(T.BARE, merged, line, col, merged))
        return True

    # -- strings ------------------------------------------------------------

    def _lex_string(self, multiline: bool) -> None:
        if self.s.peek() == '"':
            self._lex_basic(multiline)
        else:
            self._lex_literal(multiline)

    def _at_triple(self, quote: str) -> bool:
        return self.s.starts(quote * 3)

    def _lex_basic(self, multiline_hint: bool) -> None:
        s = self.s
        line, col = s.line, s.col
        if self._at_triple('"'):
            s.advance(); s.advance(); s.advance()
            value = self._read_multiline_body('"')
            self.tokens.append(Token(T.MBASIC, value, line, col, '"""'))
        else:
            s.advance()  # opening quote
            value = self._read_oneline_basic()
            self.tokens.append(Token(T.BASIC, value, line, col, '"'))

    def _lex_literal(self, multiline_hint: bool) -> None:
        s = self.s
        line, col = s.line, s.col
        if self._at_triple("'"):
            s.advance(); s.advance(); s.advance()
            value = self._read_multiline_body("'")
            self.tokens.append(Token(T.MLITERAL, value, line, col, "'''"))
        else:
            s.advance()  # opening quote
            start = s.pos
            start_line = s.line
            while not s.eof():
                ch = s.peek()
                if ch == "'":
                    raw = s.text[start:s.pos]
                    s.advance()
                    self._validate_single_line_raw(raw, start_line)
                    self.tokens.append(Token(T.LITERAL, raw, line, col, "'"))
                    return
                if ch in "\n\r":
                    raise s.err("newline in single-quoted literal string")
                code = ord(ch)
                if (code < 0x20 and ch != "\t") or code == 0x7F:
                    raise s.err("control character in literal string")
                s.advance()
            raise s.err("unterminated literal string", line, col)

    def _validate_single_line_raw(self, raw: str, start_line: int) -> None:
        for ch in raw:
            code = ord(ch)
            if (code < 0x20 and ch != "\t") or code == 0x7F:
                raise self.s.err("control character in literal string")

    def _read_oneline_basic(self) -> str:
        s = self.s
        parts: list[str] = []
        while not s.eof():
            ch = s.peek()
            if ch == '"':
                s.advance()
                return "".join(parts)
            if ch in "\n\r":
                raise s.err("newline in single-line basic string")
            code = ord(ch)
            if (code < 0x20 and ch != "\t") or code == 0x7F:
                raise s.err("control character in basic string")
            if ch == "\\":
                parts.append(self._read_escape(allow_line_trim=False))
            else:
                parts.append(s.advance())
        raise s.err("unterminated basic string")

    def _read_multiline_body(self, quote: str) -> str:
        s = self.s
        triple = quote * 3
        # A newline immediately following the opening delimiter is trimmed.
        if s.peek() == "\n":
            s.advance()
        elif s.starts("\r\n"):
            s.advance(); s.advance()

        parts: list[str] = []
        while not s.eof():
            if s.peek() == quote:
                # Count the run of quote characters. Three close the string;
                # 1-2 additional quotes are still string content (e.g.
                # ''''x'''' -> content "'x'"). Five or more consecutive
                # quotes are invalid (no room for the closing delimiter).
                run = 0
                run_line, run_col = s.line, s.col
                while s.peek() == quote:
                    s.advance()
                    run += 1
                if run >= 6:
                    raise s.err(
                        f"at most two {quote!r} may precede the closing delimiter",
                        run_line, run_col,
                    )
                if run >= 3:
                    if run > 3:
                        parts.append(quote * (run - 3))
                    text = "".join(parts)
                    return self._normalize_newlines(text)
                parts.append(quote * run)
                continue
            ch = s.peek()
            if quote == "'":
                if ch == "\r" and s.peek(1) != "\n":
                    raise s.err("bare carriage return must be part of CRLF")
                code = ord(ch)
                if code < 0x20 or code == 0x7F:
                    if ch not in "\n\r\t":
                        raise s.err("control character in multiline literal string")
                parts.append(s.advance())
                continue
            # multiline basic
            if ch == "\\":
                parts.append(self._read_escape(allow_line_trim=True))
            else:
                if ch == "\r" and s.peek(1) != "\n":
                    raise s.err("bare carriage return must be part of CRLF")
                code = ord(ch)
                if code < 0x20 or code == 0x7F:
                    if ch not in "\n\r\t":
                        raise s.err("control character in multiline basic string")
                parts.append(s.advance())
        raise s.err(f"unterminated multiline string (expected {triple})")

    # -- escapes ------------------------------------------------------------

    def _read_escape(self, allow_line_trim: bool) -> str:
        s = self.s
        s.advance()  # backslash
        if s.eof():
            raise s.err("dangling backslash")
        ch = s.peek()
        simple = {
            "b": "\b",
            "t": "\t",
            "n": "\n",
            "f": "\f",
            "r": "\r",
            '"': '"',
            "\\": "\\",
        }
        if allow_line_trim and ch in " \t":
            # Multiline line-ending backslash: backslash, then only blank
            # whitespace/comments until a newline. If a non-whitespace char
            # appears before the newline, it is NOT a valid trim (and not a
            # valid escape either) — reject it.
            return self._line_whitespace_trim_lookahead()
        s.advance()  # consume the escaped character
        if ch in simple:
            return simple[ch]
        if ch == "u":
            return self._read_unicode(4)
        if ch == "U":
            return self._read_unicode(8)
        if allow_line_trim and ch in "\n\r":
            return self._line_whitespace_trim(ch)
        raise s.err(f"invalid escape sequence \\{ch}")

    def _line_whitespace_trim_lookahead(self) -> str:
        """Handle ``\\`` followed by whitespace that must reach a newline.

        Inside a string the '#' is a literal character, not a comment, so it
        does not get special treatment here: any non-whitespace encountered
        before the line ending invalidates the line-trim backslash.
        """
        s = self.s
        while not s.eof():
            ch = s.peek()
            if ch in " \t":
                s.advance()
            elif ch in "\n\r":
                first = s.advance()
                return self._line_whitespace_trim(first)
            else:
                raise s.err("invalid escape: backslash followed by "
                            "non-whitespace before end of line")
        raise s.err("dangling line-trim backslash")

    def _line_whitespace_trim(self, first: str) -> str:
        s = self.s
        if first == "\r":
            if s.peek() != "\n":
                raise s.err("bare carriage return must be part of CRLF")
            s.advance()
        # consume the newline plus all following blank/whitespace lines.
        # '#' inside a string is content, not a comment, so it stops the trim.
        while not s.eof():
            ch = s.peek()
            if ch in " \t\r\n":
                if ch == "\r" and s.peek(1) != "\n":
                    raise s.err("bare carriage return must be part of CRLF")
                s.advance()
            else:
                break
        return ""

    def _read_unicode(self, ndigits: int) -> str:
        s = self.s
        digits = ""
        for _ in range(ndigits):
            if s.eof() or s.peek() in "\n\r":
                raise s.err("truncated unicode escape")
            digits += s.advance()
        # int(..., 16) silently accepts underscores, so validate the hex
        # alphabet explicitly to keep malformed escapes (e.g. \\u00_B4) out
        if any(c not in "0123456789abcdefABCDEF" for c in digits):
            raise s.err(
                f"invalid hex digit in unicode escape \\{'u' if ndigits == 4 else 'U'}{digits}"
            )
        code = int(digits, 16)
        if not (0 <= code <= 0x10FFFF) or 0xD800 <= code <= 0xDFFF:
            raise s.err("unicode escape out of range or surrogate")
        return chr(code)

    @staticmethod
    def _normalize_newlines(text: str) -> str:
        return text.replace("\r\n", "\n").replace("\r", "\n")
