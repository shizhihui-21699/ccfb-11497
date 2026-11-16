"""Grammar-aware and byte-level mutators.

A mutator takes a valid rendered document plus the RNG and returns a mutated
string. Strategies are registered in :data:`MUTATORS` with a relative weight;
the fuzzer draws among them dynamically. They are grouped into four layers:

* byte/character  — flip, insert, delete, duplicate a character/line
* lexical         — target quotes, escapes, brackets, numeric bases, control
* structural      — swap/remove headers, duplicate keys, alter table nesting
* semantic        — redefine tables, mutate inline-table immutability, AOT rules

Many lexical/structural mutators locate a *meaningful anchor* (a quote, a
``[``, an ``=``, a digit run) instead of mutating at a uniformly random
offset, which keeps the corpus productive rather than producing noise.
"""

from __future__ import annotations

import random
from collections import OrderedDict

# Characters with special meaning, reused by several strategies.
STRUCTURAL = "[]{}=,.#\"'"
CONTROL_BYTES = ["\x00", "\x01", "\x08", "\x0c", "\x1f", "\x7f"]
WHITESPACE = [" ", "\t", "  ", "\t\t", " \t ", "", "\n", "\r\n"]


def _splice(text: str, pos: int, delete: int, insert: str) -> str:
    return text[:pos] + insert + text[pos + delete:]


def _positions_of(text: str, chars: str) -> list[int]:
    return [i for i, c in enumerate(text) if c in chars]


# --------------------------------------------------------------------------
# byte / character layer
# --------------------------------------------------------------------------

def m_delete_char(text: str, rng: random.Random) -> str:
    pos = rng.randrange(len(text))
    return _splice(text, pos, 1, "")


def m_insert_char(text: str, rng: random.Random) -> str:
    pos = rng.randrange(len(text) + 1)
    ch = rng.choice(list(STRUCTURAL) + CONTROL_BYTES + ["a", "0", "_", "-", " "])
    return _splice(text, pos, 0, ch)


def m_flip_char(text: str, rng: random.Random) -> str:
    pos = rng.randrange(len(text))
    ch = rng.choice(list(STRUCTURAL) + CONTROL_BYTES + ["a", "0", "_"])
    return _splice(text, pos, 1, ch)


def m_duplicate_char(text: str, rng: random.Random) -> str:
    pos = rng.randrange(len(text))
    return _splice(text, pos, 0, text[pos])


def m_swap_adjacent(text: str, rng: random.Random) -> str:
    if len(text) < 2:
        return text
    pos = rng.randrange(len(text) - 1)
    a, b = text[pos], text[pos + 1]
    return text[:pos] + b + a + text[pos + 2:]


def m_whitespace(text: str, rng: random.Random) -> str:
    candidates = _positions_of(text, " \t\n")
    if not candidates:
        return text
    pos = rng.choice(candidates)
    return _splice(text, pos, 1, rng.choice(WHITESPACE))


def m_line_splice(text: str, rng: random.Random) -> str:
    lines = text.split("\n")
    if len(lines) < 2:
        return text
    action = rng.choice(["dup", "del", "swap", "blank"])
    idx = rng.randrange(len(lines))
    if action == "dup":
        lines.insert(idx, lines[idx])
    elif action == "del":
        del lines[idx]
    elif action == "swap" and idx + 1 < len(lines):
        lines[idx], lines[idx + 1] = lines[idx + 1], lines[idx]
    else:
        lines.insert(idx, "")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# lexical layer — anchor-aware
# --------------------------------------------------------------------------

def m_quote(text: str, rng: random.Random) -> str:
    positions = _positions_of(text, "\"'")
    if not positions:
        return text
    pos = rng.choice(positions)
    action = rng.choice(["remove", "duplicate", "flip", "insert-near"])
    if action == "remove":
        return _splice(text, pos, 1, "")
    if action == "duplicate":
        return _splice(text, pos, 0, text[pos])
    if action == "flip":
        new = "'" if text[pos] == '"' else '"'
        return _splice(text, pos, 1, new)
    return _splice(text, pos, 0, rng.choice(['"', "'"]))


def m_escape(text: str, rng: random.Random) -> str:
    backslashes = [i for i, c in enumerate(text) if c == "\\"]
    if backslashes:
        pos = rng.choice(backslashes)
        if pos + 1 < len(text):
            bad = rng.choice(["x", "q", "1", "a", "/", ":"])
            return _splice(text, pos + 1, 1, bad)
    # otherwise inject a dangling/invalid escape inside a basic string
    quote_positions = [i for i, c in enumerate(text) if c == '"']
    if quote_positions:
        pos = rng.choice(quote_positions)
        return _splice(text, pos, 0, rng.choice(["\\", "\\x", "\\u12"]))
    return text


def m_unicode_escape(text: str, rng: random.Random) -> str:
    samples = [
        "\\u00", "\\uZZZZ", "\\U00110000", "\\uD800", "\\UFFFFFFFF",
        "\\u000", "\\u", "\\U0000000",
    ]
    quote_positions = [i for i, c in enumerate(text) if c == '"']
    if not quote_positions:
        return text
    pos = rng.choice(quote_positions)
    return _splice(text, pos, 0, rng.choice(samples))


def m_bracket(text: str, rng: random.Random) -> str:
    positions = _positions_of(text, "[]{}")
    if not positions:
        return text
    pos = rng.choice(positions)
    action = rng.choice(["remove", "flip-open-close", "duplicate"])
    ch = text[pos]
    if action == "remove":
        return _splice(text, pos, 1, "")
    if action == "duplicate":
        return _splice(text, pos, 0, ch)
    opposite = {"[": "]", "]": "[", "{": "}", "}": "{"}[ch]
    return _splice(text, pos, 1, opposite)


def m_number(text: str, rng: random.Random) -> str:
    # find a maximal digit run and mutate its grammar
    runs = []
    i = 0
    while i < len(text):
        if text[i].isdigit():
            start = i
            while i < len(text) and (text[i].isdigit() or text[i] in "_.xXoObBeE+-"):
                i += 1
            runs.append((start, i))
        else:
            i += 1
    if not runs:
        return text
    start, end = rng.choice(runs)
    token = text[start:end]
    variants = [
        "0" + token, token + "_", "_" + token, token + ".",
        "0x" + token, "0b" + token, "0o" + token,
        token + "e", token + "e+", "+" + token, "-" + token,
        token.replace("_", "__") if "_" in token else token + "1",
    ]
    return text[:start] + rng.choice(variants) + text[end:]


def m_control_char(text: str, rng: random.Random) -> str:
    pos = rng.randrange(len(text) + 1)
    return _splice(text, pos, 0, rng.choice(CONTROL_BYTES))


def m_comment(text: str, rng: random.Random) -> str:
    lines = text.split("\n")
    idx = rng.randrange(len(lines))
    action = rng.choice(["add", "extend", "hash-in-value"])
    if action == "add":
        lines[idx] = lines[idx] + " # comment" + rng.choice(CONTROL_BYTES)
    elif action == "extend":
        lines[idx] = lines[idx] + rng.choice(["#", "  #", "#" + "x" * 5])
    else:
        lines[idx] = lines[idx].replace("=", "=#", 1)
    return "\n".join(lines)


# --------------------------------------------------------------------------
# structural / semantic layer — line and header aware
# --------------------------------------------------------------------------

def _table_header_lines(text: str) -> list[int]:
    return [i for i, line in enumerate(text.split("\n"))
            if line.strip().startswith("[")]


def m_duplicate_key(text: str, rng: random.Random) -> str:
    lines = [ln for ln in text.split("\n") if "=" in ln and not ln.strip().startswith("#")]
    if not lines:
        return text
    target = rng.choice(lines)
    return text + target + "\n"


def m_redefine_table(text: str, rng: random.Random) -> str:
    header_idx = _table_header_lines(text)
    lines = text.split("\n")
    if not header_idx:
        # turn a root key into a table header
        kv = [i for i, ln in enumerate(lines) if "=" in ln]
        if not kv:
            return text
        key = lines[rng.choice(kv)].split("=", 1)[0].strip()
        return text + f"[{key}]\n"
    idx = rng.choice(header_idx)
    header = lines[idx].strip()
    return text + header + "\n"


def m_header_to_aot(text: str, rng: random.Random) -> str:
    lines = text.split("\n")
    idx = _table_header_lines(text)
    if not idx:
        return text
    i = rng.choice(idx)
    line = lines[i]
    if line.strip().startswith("[["):
        lines[i] = line.replace("[[", "[", 1).replace("]]", "]", 1)
    else:
        lines[i] = line.replace("[", "[[", 1).replace("]", "]]", 1)
    return "\n".join(lines)


def m_inline_break(text: str, rng: random.Random) -> str:
    brace_lines = [i for i, ln in enumerate(text.split("\n")) if "{" in ln]
    if not brace_lines:
        return text
    lines = text.split("\n")
    i = rng.choice(brace_lines)
    actions = [
        lambda ln: ln.replace("{", "{\n", 1),
        lambda ln: ln.replace(",", ",\n", 1) if "," in ln else ln,
        lambda ln: ln + ", }" if ln.rstrip().endswith("}") else ln,
        lambda ln: ln.replace("}", ", }", 1),
    ]
    lines[i] = rng.choice(actions)(lines[i])
    return "\n".join(lines)


def m_dotted_key(text: str, rng: random.Random) -> str:
    lines = text.split("\n")
    kv = [i for i, ln in enumerate(lines) if "=" in ln]
    if not kv:
        return text
    i = rng.choice(kv)
    key_part = lines[i].split("=", 1)[0]
    key = key_part.strip()
    variants = [
        key_part + "." + key.split(".")[-1],
        key_part.replace(".", "..", 1) if "." in key else key + ".",
        " " + key_part,
    ]
    lines[i] = rng.choice(variants) + "=" + lines[i].split("=", 1)[1]
    return "\n".join(lines)


def m_crlf(text: str, rng: random.Random) -> str:
    action = rng.choice(["crlf", "bare-cr", "mixed"])
    if action == "crlf":
        return text.replace("\n", "\r\n")
    if action == "bare-cr":
        return text.replace("\n", "\r")
    lines = text.split("\n")
    for i in range(0, len(lines) - 1, 2):
        lines[i] = lines[i] + "\r"
    return "\n".join(lines)


#: registry: name -> (callable, relative weight)
#: Weights are tuned from measured per-strategy disagreement yield over
#: ~1M iterations (see stats.json "strategy_stats"). High-yield structural
#: and CRLF mutators are up-weighted; control-char keeps a small weight for
#: coverage even though its raw disagreement yield is low.
MUTATORS = OrderedDict([
    # byte layer — cheap, high volume
    ("delete-char", (m_delete_char, 8)),
    ("insert-char", (m_insert_char, 6)),
    ("flip-char", (m_flip_char, 6)),
    ("duplicate-char", (m_duplicate_char, 6)),
    ("swap-adjacent", (m_swap_adjacent, 6)),
    ("whitespace", (m_whitespace, 9)),
    ("line-splice", (m_line_splice, 6)),
    # lexical layer — anchor-aware, higher yield
    ("quote", (m_quote, 8)),
    ("escape", (m_escape, 7)),
    ("unicode-escape", (m_unicode_escape, 6)),
    ("bracket", (m_bracket, 5)),
    ("number", (m_number, 9)),
    ("control-char", (m_control_char, 3)),
    ("comment", (m_comment, 5)),
    # structural / semantic layer — measured highest yield
    ("duplicate-key", (m_duplicate_key, 7)),
    ("redefine-table", (m_redefine_table, 6)),
    ("header-aot", (m_header_to_aot, 9)),
    ("inline-break", (m_inline_break, 14)),
    ("dotted-key", (m_dotted_key, 7)),
    ("crlf", (m_crlf, 12)),
])


def mutate_once(text: str, rng: random.Random,
                strategy: str | None = None) -> tuple[str, str]:
    if strategy is None:
        names = list(MUTATORS)
        # character-position mutators cannot run on an empty document;
        # restrict the draw to insertion-style strategies until it grows.
        if not text:
            names = ["insert-char", "control-char", "line-splice", "comment"]
        weights = [MUTATORS[n][1] for n in names]
        strategy = rng.choices(names, weights=weights, k=1)[0]
    if not text and strategy not in ("insert-char", "control-char"):
        strategy = "insert-char"
    fn = MUTATORS[strategy][0]
    return fn(text, rng), strategy


def mutate(text: str, rng: random.Random,
           max_passes: int = 4) -> tuple[str, list[str]]:
    """Apply 1..max_passes mutations, possibly stacking strategies."""
    passes = rng.randint(1, max_passes)
    applied = []
    current = text
    for _ in range(passes):
        current, name = mutate_once(current, rng)
        applied.append(name)
    return current, applied
