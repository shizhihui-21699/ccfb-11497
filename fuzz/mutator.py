"""Grammar-aware mutation for differential fuzzing.

Two complementary layers:

* **IR mutations** operate on a parsed :class:`fuzz.ir.Document`.  They swap,
  retarget or corrupt individual syntax units (a value's spelling, a key
  path, a header kind) while keeping the surrounding structure coherent, so a
  single mutation maps to one plausible grammar change.  Some IR mutations
  stay valid; others deliberately break one rule.
* **Text mutations** work on rendered TOML bytes.  They target the lexical
  seams where parser disagreement actually shows up: delimiter deletion,
  quote/escape tampering, number boundary tricks, comment and newline
  insertion.

Every public mutator takes an RNG and returns a new object; inputs are never
mutated in place, which keeps corpus entries reusable.
"""

from __future__ import annotations

import copy
import random

from . import ir
from .generator import (
    datetime_value,
    float_value,
    int_value,
    string_value,
    value,
    _key,
)

# Deliberately "near miss" scalar spellings: each is invalid or ambiguous in a
# way that different parsers have historically handled differently.
_BAD_INTS = [
    "01", "00", "-01", "+01", "0_", "_1", "1__2", "1_", "0x", "0xG1",
    "0o9", "0b2", "-0x1", "+0o7", "0b", "99999999999999999999999999",
]
_BAD_FLOATS = [
    "03.14", "1.", ".5", "1e", "e5", "1e+", "1__2.0", "1._2", "nan_",
    "+inff", "in", "0.0e", "6.626 E-34", "1e9999",
]
_BAD_STR_DQ = ['"unterminated', '"bad \\x escape"', '"tab\there"', '""']
_BAD_DATETIMES = [
    "1979-13-01", "1979-02-30", "25:00:00", "1979-05-27", "01:02:03Z",
    "1979-05-27T", "1979/05/27", "12:61:00", "1979-05-27 25:00:00",
]


# ============================================================== IR mutations
def mutate_ir(rng: random.Random, doc: ir.Document) -> ir.Document:
    """Apply one structural mutation to a copy of ``doc``."""
    out = copy.deepcopy(doc)
    strategy = rng.choice([
        "replace-scalar", "corrupt-scalar", "retarget-key",
        "toggle-header", "drop-pair", "add-pair", "nest-array",
        "duplicate-key",
    ])

    sections_with_body = [s for s in out.sections if s.body]
    all_kvs = out.root_pairs + [kv for s in out.sections for kv in s.body]

    if strategy == "replace-scalar" and all_kvs:
        kv = rng.choice(all_kvs)
        kv.value = value(rng)
    elif strategy == "corrupt-scalar" and all_kvs:
        kv = rng.choice(all_kvs)
        kv.value = _corrupt_value(rng, kv.value)
    elif strategy == "retarget-key" and all_kvs:
        kv = rng.choice(all_kvs)
        extra = tuple(_key(rng) for _ in range(rng.randint(1, 2)))
        kv.path = kv.path + extra if rng.random() < 0.5 else extra + kv.path
    elif strategy == "toggle-header" and out.sections:
        s = rng.choice(out.sections)
        s.array = not s.array
    elif strategy == "drop-pair" and all_kvs:
        if out.root_pairs:
            out.root_pairs.remove(rng.choice(out.root_pairs))
        else:
            nonempty = next((s.body for s in out.sections if s.body), None)
            if nonempty:
                nonempty.remove(rng.choice(nonempty))
    elif strategy == "add-pair":
        host = rng.choice(out.sections) if out.sections and rng.random() < 0.7 \
            else None
        kv = ir.KeyVal((_key(rng),), value(rng))
        if host is None:
            out.root_pairs.append(kv)
        else:
            host.body.append(kv)
    elif strategy == "nest-array" and all_kvs:
        kv = rng.choice(all_kvs)
        kv.value = ir.Array([kv.value, value(rng)])
    elif strategy == "duplicate-key" and all_kvs:
        # Introduce a duplicate key/value pair (a guaranteed semantic clash).
        src = rng.choice(all_kvs)
        dup = ir.KeyVal(src.path, value(rng))
        if src in out.root_pairs:
            out.root_pairs.append(dup)
        else:
            for s in out.sections:
                if src in s.body:
                    s.body.append(dup)
                    break
    return out


def _corrupt_value(rng: random.Random, current) -> ir.Value:
    """Replace a scalar with a deliberately malformed spelling."""
    if isinstance(current, ir.Int):
        return ir.Int(rng.choice(_BAD_INTS))
    if isinstance(current, ir.Float):
        return ir.Float(rng.choice(_BAD_FLOATS))
    if isinstance(current, (ir.BasicStr, ir.LiteralStr)):
        # Keep the node type but plant invalid raw content; the renderer's
        # safety fallbacks are bypassed by using a basic node with control
        # characters that must be escaped.
        return ir.BasicStr(rng.choice(["raw\x00ctrl", "raw\x08", "\x7f"]),
                           multiline=current.multiline)
    if isinstance(current, ir.DateTime):
        return ir.DateTime(rng.choice(_BAD_DATETIMES))
    if isinstance(current, ir.Bool):
        return ir.Int(rng.choice(_BAD_INTS))
    # Containers: corrupt a random leaf inside.
    if isinstance(current, ir.Array) and current.items:
        current.items[0] = _corrupt_value(rng, current.items[0])
        return current
    if isinstance(current, ir.InlineTable) and current.pairs:
        path, val = current.pairs[0]
        current.pairs[0] = (path, _corrupt_value(rng, val))
        return current
    return ir.Int(rng.choice(_BAD_INTS))


# ============================================================ text mutations
_TEXT_OPS = [
    "delete-char", "insert-char", "swap-adjacent", "duplicate-char",
    "flip-quote", "tamper-delimiter", "insert-newline", "insert-comment",
    "number-trick", "crlf", "control-char",
]


def mutate_text(rng: random.Random, text: str) -> str:
    """Apply one lexical-seam mutation to rendered TOML text."""
    if not text:
        return rng.choice(["a = 1\n", "[t]\n", 'x = "v"\n'])
    chars = list(text)
    op = rng.choice(_TEXT_OPS)
    pos = rng.randrange(len(chars))

    if op == "delete-char":
        del chars[pos]
    elif op == "insert-char":
        chars.insert(pos, rng.choice(" \t#=[].{},\"'-\n\r_+eE0123456789"))
    elif op == "swap-adjacent" and pos + 1 < len(chars):
        chars[pos], chars[pos + 1] = chars[pos + 1], chars[pos]
    elif op == "duplicate-char":
        chars.insert(pos, chars[pos])
    elif op == "flip-quote":
        if chars[pos] == '"':
            chars[pos] = "'"
        elif chars[pos] == "'":
            chars[pos] = '"'
    elif op == "tamper-delimiter":
        token = rng.choice(['"', "'", '"""', "'''", "[", "]", "{", "}",
                            "=", ".", ",", "]]", "[[", "\n"])
        where = rng.randrange(len(chars) + 1)
        chars.insert(where, token)
    elif op == "insert-newline":
        chars.insert(pos, rng.choice(["\n", "\r", "\r\n"]))
    elif op == "insert-comment":
        chars.insert(pos, rng.choice(["#", " # ", "\n# comment\n"]))
    elif op == "number-trick":
        trick = rng.choice(_BAD_INTS + _BAD_FLOATS)
        chars.insert(pos, trick)
    elif op == "crlf":
        # Convert a single LF into CRLF or a bare CR.
        if chars[pos] == "\n":
            chars[pos] = rng.choice(["\r\n", "\r"])
        else:
            chars.insert(pos, "\r")
    elif op == "control-char":
        chars.insert(pos, rng.choice(["\x00", "\x01", "\x08", "\x0c", "\x7f"]))
    return "".join(chars)


# ============================================================== mixed driver
def mutate(rng: random.Random, doc: ir.Document | None, text: str,
           ir_bias: float = 0.6) -> str:
    """Pick an IR or text mutation and return rendered TOML.

    ``ir_bias`` is the probability of choosing a structural (IR-level)
    mutation over a raw text mutation.
    """
    if doc is not None and rng.random() < ir_bias:
        return ir.render(mutate_ir(rng, doc))
    return mutate_text(rng, text)
