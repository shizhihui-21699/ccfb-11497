"""Differential comparison and finding classification.

A single input is run through every parser.  Parsers can legitimately disagree
on *error messages*, so only two things count as a signal:

1. an **accept/reject split** -- one parser accepts what another rejects;
2. a **value split** -- two parsers accept but produce different data.

A parser raising a non-document exception (an internal crash) is always a
finding regardless of what the others do.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .adapters import ALL_PARSERS, PARSED, REJECTED, CRASHED, Outcome

# Finding categories, ordered by severity for reporting.
CRASH = "crash"
ACCEPT_REJECT = "accept-reject"
VALUE_SPLIT = "value-split"
KNOWN_DIVERGENCE = "known-divergence"
# Within accept/reject, which side is the odd one out matters for triage.
ZTOML_ACCEPTS = "ztoml-accepts-others-reject"   # possible security bypass
ZTOML_REJECTS = "ztoml-rejects-others-accept"  # possible over-strictness
THIRD_PARTY_SPLIT = "third-party-split"        # reference libs disagree

# A time-like token where the seconds component is absent (TOML 1.1-only).
_NO_SECS = re.compile(r"\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}(?!\s*:)|(?<![\d:])\d{2}:\d{2}(?![\d:])")
# Exponent-bearing float literal, used to detect over/underflow spellings.
# Underscores are legal within the exponent (e.g. 1e5_25 == 1e525).
_EXP_FLOAT = re.compile(r"[0-9][0-9_]*(?:\.[0-9_]+)?[eE]([+-]?[0-9][0-9_]*)")


def _is_known_divergence(text: str, sub: str,
                         outcomes: dict[str, Outcome]) -> str | None:
    """Return a known-divergence name if this split is expected, else None."""
    # 1) tomllib accepts seconds-less times (a TOML 1.1 feature); ztoml/rtoml
    #    reject.  Only counts when ztoml is on the rejecting side.
    if sub == ZTOML_REJECTS and _NO_SECS.search(text):
        tomllib_o = outcomes.get("tomllib")
        if tomllib_o is not None and tomllib_o.kind == PARSED:
            return "optional-seconds-toml11"
    # 2) rtoml rejects out-of-range numbers; tomllib/ztoml keep them.  Floats
    #    map to inf; integers keep arbitrary precision (toml-test states int64
    #    support "is not mandatory", so Python big-ints are conforming).
    if sub == ZTOML_ACCEPTS:
        rtoml_o = outcomes.get("rtoml")
        if rtoml_o is not None and rtoml_o.kind == REJECTED:
            detail = rtoml_o.detail.lower()
            if "overflow" in detail and _has_overflow(text):
                return "float-overflow"
            if "integer number overflowed" in detail:
                return "integer-overflow"
    # 3) TOML 1.1-only escape sequences (\e, \x, ...) are invalid under 1.0,
    #    so ztoml rejects while the 1.1-aware references accept them.
    if sub == ZTOML_REJECTS and _has_11_only_escape(text):
        return "escape-1.1-only"
    # 4) Newlines inside an inline table (outside a multiline-string value)
    #    are forbidden in TOML 1.0 but allowed in 1.1.  ztoml enforces 1.0;
    #    tomllib already follows 1.1 and accepts them.  Recognized by a
    #    linebreak between { and } together with ztoml's brace/key errors.
    if sub == ZTOML_REJECTS and _has_inline_table_newline(text, outcomes):
        return "inline-table-newline-toml11"
    # 5) A trailing comma inside an inline table is forbidden in 1.0 (allowed
    #    in 1.1).  Arrays keep their trailing comma in both versions.
    if sub == ZTOML_REJECTS and _has_inline_trailing_comma(text, outcomes):
        return "inline-table-trailing-comma-toml11"
    return None


def _has_inline_trailing_comma(text: str,
                               outcomes: dict[str, Outcome]) -> bool:
    z = outcomes.get("ztoml")
    if z is None or z.kind != REJECTED or "trailing comma" not in z.detail:
        return False
    # Confirm the comma sits inside braces rather than an array.
    depth_brace = 0
    depth_bracket = 0
    for i, ch in enumerate(text):
        if ch == "{":
            depth_brace += 1
        elif ch == "}":
            depth_brace = max(0, depth_brace - 1)
        elif ch == "[":
            depth_bracket += 1
        elif ch == "]":
            depth_bracket = max(0, depth_bracket - 1)
        elif ch == "," and depth_brace > 0:
            nxt = text[i + 1:].lstrip()
            if nxt.startswith("}"):
                return True
    return False


_11_ONLY_ESCAPES = ("\\e", "\\x")


def _has_11_only_escape(text: str) -> bool:
    return any(seq in text for seq in _11_ONLY_ESCAPES)


def _has_inline_table_newline(text: str,
                              outcomes: dict[str, Outcome]) -> bool:
    z = outcomes.get("ztoml")
    if z is None or z.kind != REJECTED:
        return False
    # Any linebreak sitting between an inline-table's braces counts; the
    # precise error wording varies (expected '}', expected value, invalid key
    # character, ...) depending on where the newline landed.
    depth = 0
    for ch in text:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        elif ch in ("\n", "\r") and depth > 0:
            return True
    return False


def _has_overflow(text: str) -> bool:
    # Underscores are legal inside a number (1e5_25 == 1e525), so strip them
    # before reading the exponent to avoid missing underscored spellings.
    for m in _EXP_FLOAT.finditer(text):
        try:
            exp = int(m.group(1).replace("_", ""))
        except ValueError:
            continue
        if abs(exp) > 308:
            return True
    return False


@dataclass
class Finding:
    category: str
    subcategory: str
    input_text: str
    outcomes: dict[str, Outcome]
    # The parser names that form the minority view (used to decide blame).
    minority: tuple[str, ...] = ()
    signature: str = ""
    labels: list[str] = field(default_factory=list)

    def to_record(self) -> dict:
        return {
            "category": self.category,
            "subcategory": self.subcategory,
            "signature": self.signature,
            "labels": self.labels,
            "minority": list(self.minority),
            "input": self.input_text,
            "outcomes": {
                name: {"kind": o.kind, "payload": _jsonable(o.payload),
                       "detail": o.detail}
                for name, o in self.outcomes.items()
            },
        }


def _jsonable(obj):
    if isinstance(obj, tuple):
        return [_jsonable(x) for x in obj]
    if isinstance(obj, list):
        return [_jsonable(x) for x in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return repr(obj)


def run_all(text: str, parsers=None) -> dict[str, Outcome]:
    parsers = parsers or ALL_PARSERS
    return {name: fn(text) for name, fn in parsers.items()}


def classify(text: str, outcomes: dict[str, Outcome],
             trio=("ztoml", "tomllib", "rtoml")) -> Finding | None:
    """Return a Finding if the parsers disagree, else None."""
    # --- crashes always win, regardless of agreement elsewhere ---
    crashed = {n: o for n, o in outcomes.items() if o.kind == CRASHED}
    if crashed:
        names = tuple(sorted(crashed))
        return Finding(
            category=CRASH,
            subcategory=",".join(names),
            input_text=text,
            outcomes=outcomes,
            minority=names,
            signature=_crash_signature(crashed),
            labels=_grammar_labels(text),
        )

    kinds = {n: o.kind for n, o in outcomes.items()}
    parsed_names = {n for n, k in kinds.items() if k == PARSED}
    rejected_names = {n for n, k in kinds.items() if k == REJECTED}

    # --- accept/reject split ---
    if parsed_names and rejected_names:
        sub, minority = _blame(parsed_names, rejected_names, trio)
        known = _is_known_divergence(text, sub, outcomes)
        if known is not None:
            return Finding(
                category=KNOWN_DIVERGENCE,
                subcategory=known,
                input_text=text,
                outcomes=outcomes,
                minority=tuple(sorted(minority)),
                signature=f"known|{known}",
                labels=_grammar_labels(text),
            )
        return Finding(
            category=ACCEPT_REJECT,
            subcategory=sub,
            input_text=text,
            outcomes=outcomes,
            minority=tuple(sorted(minority)),
            signature=_accept_reject_signature(text, sub, outcomes),
            labels=_grammar_labels(text),
        )

    # --- value split (only meaningful when all accepted) ---
    if len(parsed_names) >= 2:
        payloads = {n: outcomes[n].payload for n in parsed_names}
        distinct = set(payloads.values())
        if len(distinct) > 1:
            minority = _value_minority(payloads)
            return Finding(
                category=VALUE_SPLIT,
                subcategory="distinct-values",
                input_text=text,
                outcomes=outcomes,
                minority=tuple(sorted(minority)),
                signature=_value_signature(payloads),
                labels=_grammar_labels(text),
            )
    return None


def _blame(parsed: set[str], rejected: set[str], trio) -> tuple[str, set[str]]:
    """Decide which parser(s) hold the minority accept/reject view."""
    z = "ztoml"
    if z in parsed and z not in rejected:
        # Our parser accepts while references reject: the dangerous direction.
        return ZTOML_ACCEPTS, {z}
    if z in rejected:
        return ZTOML_REJECTS, {z}
    # ztoml not involved: the reference libraries disagree among themselves.
    minority = parsed if len(parsed) < len(rejected) else rejected
    return THIRD_PARTY_SPLIT, minority


def _value_minority(payloads: dict) -> set[str]:
    # Group parsers by their produced value; the smallest group is minority.
    groups: dict = {}
    for name, payload in payloads.items():
        groups.setdefault(payload, set()).add(name)
    return min(groups.values(), key=len)


# ------------------------------------------------------------- signatures
def _crash_signature(crashed: dict[str, Outcome]) -> str:
    parts = [f"{n}:{o.payload}" for n, o in sorted(crashed.items())]
    return "crash|" + "|".join(parts)


def _accept_reject_signature(text: str, sub: str,
                             outcomes: dict[str, Outcome]) -> str:
    # The signature abstracts away exact positions/messages so the same class
    # of input dedupes: category + the set of rejecting parser error classes.
    rejects = sorted(
        f"{n}:{o.payload}" for n, o in outcomes.items() if o.kind == REJECTED
    )
    return f"ar|{sub}|" + ";".join(rejects) + "|" + _shape_hint(text)


def _value_signature(payloads: dict) -> str:
    tags = sorted(
        f"{n}:{_top_type(p)}" for n, p in payloads.items()
    )
    return "value|" + ";".join(tags)


def _top_type(payload) -> str:
    if isinstance(payload, tuple) and payload:
        return str(payload[0])
    return type(payload).__name__


def _shape_hint(text: str) -> str:
    return ",".join(sorted(set(_grammar_labels(text))))


# ----------------------------------------------------------------- labeling
def _grammar_labels(text: str) -> list[str]:
    """Coarse grammar fingerprints used to bucket findings, not signatures."""
    labels = []
    checks = [
        ('"""', "multiline-basic"),
        ("'''", "multiline-literal"),
        ("[[", "array-of-tables"),
        ("{", "inline-table"),
        ("#", "comment"),
        ("\r", "carriage-return"),
        ("\\u", "unicode-escape"),
        ("\\", "backslash-escape"),
        ("0x", "hex-int"),
        ("0o", "oct-int"),
        ("0b", "bin-int"),
        ("e", "exponent"),
        ("-", "date-or-sign"),
        (":", "time-like"),
        ("nan", "nan"),
        ("inf", "inf"),
        ("[", "array"),
        (".", "dotted-key"),
    ]
    for needle, label in checks:
        if needle in text:
            labels.append(label)
    if any(ord(c) < 0x20 and c not in "\t\n\r" for c in text):
        labels.append("raw-control-char")
    return labels
