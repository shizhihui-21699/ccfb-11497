"""Random-but-valid TOML document generator.

The generator emits :class:`fuzz.ir.Document` objects that render to TOML 1.0
text every reference parser accepts.  It deliberately over-samples the awkward
parts of the grammar (dotted keys, AOT nesting, temporal values, escape
sequences) where parsers historically disagree.
"""

from __future__ import annotations

import random
import string

from . import ir

_KEY_ALPHABET = string.ascii_letters + string.digits + "_-"
_BARE_KEY_START = string.ascii_letters + "_"


def _is_bare(key: str) -> bool:
    return bool(key) and key[0] in _BARE_KEY_START and \
        all(c in _KEY_ALPHABET for c in key)


def _key(rng: random.Random) -> str:
    if rng.random() < 0.15:
        # Quoted keys, occasionally with punctuation.
        return rng.choice(["key with space", "k.d", 'q"q', "é-键", "dot.end"])
    length = rng.randint(1, 8)
    first = rng.choice(_BARE_KEY_START)
    rest = "".join(rng.choice(_KEY_ALPHABET) for _ in range(length - 1))
    return first + rest


def _path(rng: random.Random, max_depth: int = 3) -> tuple[str, ...]:
    return tuple(_key(rng) for _ in range(rng.randint(1, max_depth)))


# ------------------------------------------------------------------ scalars
def int_value(rng: random.Random) -> ir.Int:
    style = rng.choices(
        ["dec", "underscore", "hex", "oct", "bin", "zero", "big", "signed"],
        weights=[25, 15, 15, 10, 10, 8, 9, 8],
        k=1,
    )[0]
    if style == "zero":
        return ir.Int("0")
    if style == "dec":
        return ir.Int(str(rng.randint(-99999, 99999)))
    if style == "signed":
        return ir.Int(rng.choice(["+", "-"]) + str(rng.randint(1, 9999)))
    if style == "underscore":
        groups = rng.randint(2, 4)
        # First group must not start with 0 unless it is the single digit 0,
        # so the whole spelling keeps a legal decimal leading digit.
        first = str(rng.randint(1, 999))
        rest = [
            "".join(rng.choice(string.digits) for _ in range(rng.randint(1, 3)))
            for _ in range(groups - 1)
        ]
        return ir.Int("_".join([first, *rest]))
    if style == "hex":
        digits = "".join(rng.choice("0123456789abcdefABCDEF")
                         for _ in range(rng.randint(1, 8)))
        return ir.Int("0x" + digits)
    if style == "oct":
        digits = "".join(rng.choice("01234567") for _ in range(rng.randint(1, 6)))
        return ir.Int("0o" + digits)
    if style == "bin":
        digits = "".join(rng.choice("01") for _ in range(rng.randint(1, 12)))
        return ir.Int("0b" + digits)
    return ir.Int(str(rng.randint(-(10**18), 10**18)))


def float_value(rng: random.Random) -> ir.Float:
    style = rng.choices(
        ["plain", "exp", "underscore", "special", "negzero"],
        weights=[35, 30, 15, 15, 5],
        k=1,
    )[0]
    if style == "special":
        return ir.Float(rng.choice(["inf", "+inf", "-inf", "nan", "+nan", "-nan"]))
    if style == "negzero":
        return ir.Float("-0.0")
    whole = rng.randint(0, 9999)
    frac = "".join(rng.choice(string.digits) for _ in range(rng.randint(1, 6)))
    if style == "exp":
        exp = rng.randint(-20, 20)
        return ir.Float(f"{whole}.{frac}e{exp:+d}")
    if style == "underscore":
        # Leading digit must be non-zero so no leading-zero spelling arises.
        whole = rng.randint(1, 9999)
        tail = rng.randint(0, 9)
        return ir.Float(f"{whole}_{tail}.{frac}")
    return ir.Float(f"{whole}.{frac}")


def _string_content(rng: random.Random, multiline: bool) -> str:
    safe_punct = [" ", "\t", "/", ":", ".", "#", "=", "[", "]", "🎉", "é", "中文"]
    n = rng.randint(0, 6 if multiline else 4)
    parts = [rng.choice(safe_punct + ["hello", "world", "TOML", "", "x"])
             for _ in range(n)]
    if multiline and rng.random() < 0.6:
        parts.insert(rng.randint(0, len(parts)), "\n")
    # Escapable controls only -- they are emitted via \\u / short escapes by
    # the basic-string renderer, never as raw bytes in a literal string.
    if rng.random() < 0.15:
        parts.append(rng.choice(['"', "\\", "\b", "\f", "\r"]))
    return "".join(parts)


def string_value(rng: random.Random, *, allow_multiline: bool = True) -> ir.Value:
    flavour = rng.choices(
        ["basic", "literal", "ml-basic", "ml-literal"],
        weights=[35, 25, 25 if allow_multiline else 0, 15 if allow_multiline else 0],
        k=1,
    )[0]
    if flavour == "basic":
        return ir.BasicStr(_string_content(rng, False), multiline=False)
    if flavour == "literal":
        return ir.LiteralStr(_string_content(rng, False), multiline=False)
    if flavour == "ml-basic":
        return ir.BasicStr(_string_content(rng, True), multiline=True)
    return ir.LiteralStr(_string_content(rng, True), multiline=True)


def datetime_value(rng: random.Random) -> ir.DateTime:
    y = rng.randint(1, 9999)
    mo = rng.randint(1, 12)
    d = rng.randint(1, 28)
    h, mi, s = rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 59)
    frac = ""
    if rng.random() < 0.5:
        frac = "." + "".join(rng.choice(string.digits)
                             for _ in range(rng.randint(1, 6)))
    offset = rng.choice(
        ["", "Z", "z",
         f"{rng.choice(['+', '-'])}{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}"]
    )
    kind = rng.choice(["datetime", "datetime", "local-dt", "date", "time"])
    if kind == "date":
        return ir.DateTime(f"{y:04d}-{mo:02d}-{d:02d}")
    if kind == "time":
        # Local time carries neither Z nor an offset.
        return ir.DateTime(f"{h:02d}:{mi:02d}:{s:02d}{frac}")
    sep = rng.choice(["T", "t", " "])
    if kind == "local-dt":
        return ir.DateTime(f"{y:04d}-{mo:02d}-{d:02d}{sep}{h:02d}:{mi:02d}:{s:02d}{frac}")
    return ir.DateTime(
        f"{y:04d}-{mo:02d}-{d:02d}{sep}{h:02d}:{mi:02d}:{s:02d}{frac}{offset or 'Z'}"
    )


# ---------------------------------------------------------------- containers
def scalar_value(rng: random.Random, *, inline: bool = False) -> ir.Value:
    return rng.choices(
        [lambda: int_value(rng),
         lambda: float_value(rng),
         lambda: ir.Bool(rng.random() < 0.5),
         lambda: string_value(rng, allow_multiline=not inline),
         lambda: datetime_value(rng)],
        weights=[25, 22, 10, 28, 15],
        k=1,
    )[0]()


def value(rng: random.Random, depth: int = 0, *, inline: bool = False) -> ir.Value:
    if depth >= 3:
        return scalar_value(rng, inline=inline)
    if inline:
        # Everything reachable from an inline table must render on one line:
        # single-line strings only, shallow nested containers.
        pick = rng.choices(["scalar", "array", "inline"],
                           weights=[60, 22, 18], k=1)[0]
        if pick == "scalar":
            return scalar_value(rng, inline=True)
        if pick == "array":
            return ir.Array([scalar_value(rng, inline=True)
                             for _ in range(rng.randint(0, 4))])
        return ir.InlineTable(_inline_pairs(rng, depth + 1))
    pick = rng.choices(
        ["scalar", "array", "inline"],
        weights=[70, 18, 12],
        k=1,
    )[0]
    if pick == "scalar":
        return scalar_value(rng)
    if pick == "array":
        n = rng.randint(0, 5)
        # Arrays are usually homogeneous, sometimes not (legal in TOML).
        if rng.random() < 0.75:
            template = scalar_value(rng)
            items = [_shallow_copy(template, rng) for _ in range(n)]
        else:
            items = [value(rng, depth + 1) for _ in range(n)]
        return ir.Array(items)
    return ir.InlineTable(_inline_pairs(rng, depth + 1))


def _shallow_copy(template: ir.Value, rng: random.Random) -> ir.Value:
    # Re-roll scalars of the same node type so homogeneous arrays vary.
    if isinstance(template, ir.Int):
        return int_value(rng)
    if isinstance(template, ir.Float):
        return float_value(rng)
    if isinstance(template, ir.Bool):
        return ir.Bool(rng.random() < 0.5)
    if isinstance(template, (ir.BasicStr, ir.LiteralStr)):
        return string_value(rng)
    if isinstance(template, ir.DateTime):
        return datetime_value(rng)
    return template


def _inline_pairs(rng: random.Random, depth: int) -> list[tuple[tuple[str, ...], ir.Value]]:
    pairs = []
    used: set[str] = set()
    for _ in range(rng.randint(0, 4)):
        k = _fresh_key(rng, used)
        path = (k,) + (() if rng.random() < 0.6 else (_key(rng),))
        pairs.append((path, value(rng, depth, inline=True)))
    return pairs


# ----------------------------------------------------------------- documents
class _Node:
    """A node in the synthetic table tree used while generating a document."""

    __slots__ = ("tables", "aots", "scalars")

    def __init__(self) -> None:
        self.tables: dict[str, "_Node"] = {}
        self.aots: dict[str, list["_Node"]] = {}
        self.scalars: set[str] = set()


def document(rng: random.Random, max_sections: int = 6) -> ir.Document:
    doc = ir.Document()
    root = _Node()

    # Root-level scalar pairs (never collide with generated table paths).
    root_keys: set[str] = set()
    for _ in range(rng.randint(0, 5)):
        k = _fresh_key(rng, root_keys)
        root.scalars.add(k)
        doc.root_pairs.append(ir.KeyVal((k,), value(rng)))

    # Build sections by walking/creating a real table tree, so every header
    # path is guaranteed to resolve against tables (never over a scalar).
    for _ in range(rng.randint(0, max_sections)):
        node = root
        path: list[str] = []
        # Sometimes nest under an existing table/AOT element.
        depth = rng.randint(1, 3)
        for level in range(depth):
            if node.aots and rng.random() < 0.4:
                k = rng.choice(list(node.aots))
                node = node.aots[k][-1]
                path.append(k)
                continue
            available = [k for k in node.tables]
            if available and rng.random() < 0.5:
                k = rng.choice(available)
                node = node.tables[k]
                path.append(k)
                continue
            k = _fresh_key(rng, set(node.tables) | set(node.aots) | node.scalars)
            child = _Node()
            node.tables[k] = child
            node = child
            path.append(k)

        array = rng.random() < 0.35
        leaf = _fresh_key(rng, set(node.tables) | set(node.aots) | node.scalars)
        if array:
            element = _Node()
            node.aots.setdefault(leaf, []).append(element)
            target = element
        else:
            target = _Node()
            node.tables[leaf] = target
        full_path = tuple(path + [leaf])
        section = ir.TableHeader(path=full_path, array=array)

        local_keys: set[str] = set()
        for _ in range(rng.randint(0, 4)):
            k = _fresh_key(rng, local_keys)
            local_keys.add(k)
            target.scalars.add(k)
            dotted = rng.random() < 0.3
            kv_path = (k,) if not dotted else (k, _key(rng))
            section.body.append(ir.KeyVal(kv_path, value(rng)))
        doc.sections.append(section)
    return doc


def _fresh_key(rng: random.Random, used: set[str]) -> str:
    for _ in range(20):
        k = _key(rng)
        if k and k not in used:
            used.add(k)
            return k
    k = f"k{rng.randrange(10**9)}"
    used.add(k)
    return k
