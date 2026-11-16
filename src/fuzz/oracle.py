"""Differential oracle: decide whether a set of engine results disagree and
classify the kind of disagreement.

Verdicts
--------
``consensus``      all engines accept with equal values, or all reject
``accept-split``   some accept, some reject (the dangerous class: a parser
                   bypass or a false rejection)
``value-split``    all accept but at least two distinct value trees result
``crash``          at least one engine raised a non-parser exception
``precision``      values equal after normalization, but fractional-second
                   precision differed (informational only)

When engines split, the majority behavior is treated as the reference
expectation; a 2-vs-2 tie is reported explicitly rather than guessed.
"""

from __future__ import annotations

import dataclasses
from collections import Counter

from .engines import EngineResult


@dataclasses.dataclass
class Verdict:
    kind: str
    signature: str
    detail: str
    results: dict  # name -> EngineResult
    reference_ok: bool | None      # majority accept/reject, None on a tie
    reference_canonical: object = None


def _acceptance_vector(results: list[EngineResult]) -> tuple[str, ...]:
    return tuple(r.name for r in results if r.ok)


def classify(results: list[EngineResult]) -> Verdict:
    by_name = {r.name: r for r in results}
    names = tuple(sorted(by_name))
    crashes = [r for r in results if r.crash]
    if crashes:
        sig = "crash:" + ",".join(
            f"{r.name}:{r.error_type}" for r in crashes
        )
        return Verdict(
            "crash", sig,
            "engine crash: " + "; ".join(f"{r.name} {r.crash}" for r in crashes),
            by_name, None,
        )

    accepted = [r for r in results if r.ok]
    rejected = [r for r in results if not r.ok]

    # everyone rejects: ordinary agreement on invalidity
    if not accepted:
        return Verdict(
            "consensus", "all-reject",
            "all engines rejected", by_name, False,
        )
    # everyone accepts — compare values
    if not rejected:
        trees = Counter(r.canonical for r in accepted)
        if len(trees) == 1:
            note_kinds = {n for r in accepted for n in r.precision_notes}
            if note_kinds:
                return Verdict("precision", "precision-difference",
                               "equal values, differing sub-microsecond precision",
                               by_name, True, accepted[0].canonical)
            return Verdict("consensus", "all-accept",
                           "all engines agree", by_name, True,
                           accepted[0].canonical)
        # value disagreement among accepting engines
        groups = _value_groups(accepted)
        sig = "value:" + _groups_signature(groups)
        detail = "; ".join(f"[{', '.join(g)}] share one value tree" for g in groups)
        return Verdict("value-split", sig, detail, by_name, True,
                       _majority_tree(accepted))

    # mixed acceptance
    accept_names = tuple(sorted(r.name for r in accepted))
    reject_names = tuple(sorted(r.name for r in rejected))
    tie = len(accepted) == len(rejected)
    sig = f"accept-split:accept={'+'.join(accept_names)}|reject={'+'.join(reject_names)}"
    detail = (f"accepted by {accept_names}, rejected by {reject_names}"
              + (" (TIED, no majority)" if tie else ""))
    ref_ok = None if tie else (len(accepted) > len(rejected))
    ref_tree = _majority_tree(accepted) if accepted else None
    return Verdict("accept-split", sig, detail, by_name, ref_ok, ref_tree)


def _value_groups(accepted: list[EngineResult]) -> list[list[str]]:
    seen: dict = {}
    for r in accepted:
        seen.setdefault(r.canonical, []).append(r.name)
    return [sorted(v) for v in seen.values()]


def _groups_signature(groups: list[list[str]]) -> str:
    # stable ordering regardless of which engine landed in which group
    return "|".join(",".join(g) for g in sorted(groups))


def _majority_tree(accepted: list[EngineResult]):
    counts = Counter(r.canonical for r in accepted)
    return counts.most_common(1)[0][0]
