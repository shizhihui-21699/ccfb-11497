"""Test-case minimization for differential disagreements.

Algorithm: layered delta debugging (ddmin). A discrepancy predicate is
first minimized at the *statement/line* level, then at the *character*
level. Running line minimization first keeps surviving cases close to
human-readable TOML instead of opaque byte fragments, while the character
pass still shrinks within the offending line.

Each layer is the classic ddmin loop: partition the removable units into
``n`` chunks, try removing each chunk and every other chunk; if some
removal preserves the discrepancy, restart with the smaller input and reset
``n`` to 2, otherwise grow ``n``. This converges to a 1-minimal-ish result
with no grammar assumptions beyond "lines" and "characters".
"""

from __future__ import annotations

from typing import Callable

from .engines import EngineResult

DisagreeFn = Callable[[str], bool]


def _ddmin(units: list, keep: DisagreeFn, reconstruct) -> list:
    """Classic ddmin over a list of removable units.

    ``keep(text)`` returns True when a candidate still reproduces the
    discrepancy; ``reconstruct(units)`` rebuilds the text from surviving
    units.
    """
    n = 2
    while len(units) >= 2 and n <= len(units):
        chunk_size = len(units) // n
        if chunk_size == 0:
            break
        chunks = [units[i * chunk_size:(i + 1) * chunk_size]
                  for i in range(n)]
        # carry the uneven tail into the last chunk
        tail = units[n * chunk_size:]
        if tail:
            chunks[-1] = chunks[-1] + tail

        reduced = False
        # 1. try removing one chunk at a time
        for i in range(len(chunks)):
            candidate = [u for j, c in enumerate(chunks) if j != i for u in c]
            if keep(reconstruct(candidate)):
                units = candidate
                n = 2
                reduced = True
                break
        if reduced:
            continue

        # 2. try keeping only one chunk (remove all others)
        for i in range(len(chunks)):
            candidate = list(chunks[i])
            if keep(reconstruct(candidate)):
                units = candidate
                n = max(n - 1, 2)
                reduced = True
                break
        if reduced:
            continue

        # 3. nothing worked at this granularity: refine
        if n >= len(units):
            break
        n = min(n * 2, len(units))
    return units


def _line_reconstruct(lines: list[str]) -> str:
    return "\n".join(lines)


def _char_reconstruct(chars: list[str]) -> str:
    return "".join(chars)


def minimize(text: str, still_disagrees: DisagreeFn) -> str:
    """Reduce ``text`` while ``still_disagrees`` stays True.

    Every layer is guarded: if a minimization pass produces output that no
    longer reproduces the discrepancy, that pass is discarded and the
    previous (known-good) text is kept. This guards against predicate edge
    cases where a smaller input happens to parse differently from intended.
    """
    if not still_disagrees(text):
        return text  # predicate already fails; nothing to minimize safely

    original = text

    # ---- layer 1: line/statement level ----
    lines = text.split("\n")
    reduced_lines = _ddmin(lines, still_disagrees, _line_reconstruct)
    line_text = _line_reconstruct(reduced_lines)
    if still_disagrees(line_text):
        text = line_text

    # ---- layer 2: character level within the surviving lines ----
    chars = list(text)
    reduced_chars = _ddmin(chars, still_disagrees, _char_reconstruct)
    char_text = _char_reconstruct(reduced_chars)
    if still_disagrees(char_text):
        text = char_text

    # final safety net: never return a non-reproducing "minimization"
    if not still_disagrees(text):
        return original.strip() + "\n"
    return text.strip() + "\n"


def discrepancy_predicate(
    run_all: Callable[[str], list[EngineResult]],
    reference_signature: str,
) -> DisagreeFn:
    """Build a predicate that stays True only while the *same class* of
    disagreement still reproduces (engine accept/reject split or a value
    split matching the original verdict signature)."""
    from .oracle import classify

    def keep(candidate_text: str) -> bool:
        try:
            results = run_all(candidate_text)
        except Exception:  # noqa: BLE001 - a throwable candidate just fails
            return False
        verdict = classify(results)
        if verdict.kind in ("consensus", "precision"):
            return False
        # require the same disagreement *shape* so minimization cannot
        # drift onto a different, unrelated bug
        return verdict.signature == reference_signature

    return keep
