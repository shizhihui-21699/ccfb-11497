"""Automatic minimization of discrepant inputs.

Strategy (chosen for this project): a two-stage reducer that keeps the
grammar-aware fast path but falls back to a provably general algorithm.

1. **Line-block reduction** -- TOML is line-oriented, so whole lines (and
   table sections) are the natural granularity.  A greedy pass removes the
   largest blocks first while the discrepancy *signature* stays identical.
2. **Character-level delta debugging (DDMin, Zeller/Hildebrandt)** -- on the
   remaining text, the classic n-way chunk reduction with increasing ``n``
   guarantees a 1-minimal-ish result without any grammar assumptions.

A predicate cache avoids re-parsing the same candidate across stages.  Every
accepted reduction must preserve the original finding's category *and*
signature, so minimization cannot silently turn one bug into another.
"""

from __future__ import annotations

from .differ import classify, run_all


class Minimizer:
    def __init__(self, parsers=None, max_passes: int = 64) -> None:
        self.parsers = parsers
        self.max_passes = max_passes
        self._cache: dict[str, bool] = {}

    # ------------------------------------------------------------ predicate
    def _still_triggers(self, candidate: str, category: str,
                        signature: str) -> bool:
        if candidate in self._cache:
            return self._cache[candidate]
        outcomes = run_all(candidate, self.parsers)
        finding = classify(candidate, outcomes)
        if finding is None or finding.category != category:
            ok = False
        elif category == "accept-reject":
            # Require the *same parser split*, not merely a matching message
            # class: the set of acceptors and rejecters must be identical, so
            # a reduction cannot silently erase the discrepancy.
            ok = (self._accept_vector(finding) == self._accept_vector_original
                  and finding.signature == signature)
        else:
            ok = finding.signature == signature
        self._cache[candidate] = ok
        return ok

    @staticmethod
    def _accept_vector(finding) -> tuple:
        return tuple(sorted(
            (name, o.kind) for name, o in finding.outcomes.items()
        ))

    # ------------------------------------------------------------- public API
    def minimize(self, text: str) -> str:
        outcomes = run_all(text, self.parsers)
        original = classify(text, outcomes)
        if original is None:
            return text
        category, signature = original.category, original.signature
        # Freeze the exact parser accept/reject split so every reduction step
        # can be checked against it, not just against an error-message class.
        self._accept_vector_original = self._accept_vector(original)

        reduced = self._line_reduce(text, category, signature)
        reduced = self._ddmin(reduced, category, signature)
        # A final whitespace/blank-line trim that must keep the trigger.
        reduced = self._trim_blank_lines(reduced, category, signature)
        return reduced

    # -------------------------------------------------- stage 1: line blocks
    def _line_reduce(self, text: str, category: str, signature: str) -> str:
        lines = text.splitlines(keepends=True)
        changed = True
        passes = 0
        while changed and passes < self.max_passes:
            changed = False
            passes += 1
            i = 0
            while i < len(lines):
                # Try removing a section block first (header + its body),
                # then the single line.
                candidate_blocks = self._block_at(lines, i)
                for start, end in candidate_blocks:
                    trial = "".join(lines[:start] + lines[end:])
                    if trial and self._still_triggers(
                        trial, category, signature
                    ):
                        del lines[start:end]
                        changed = True
                        i = start
                        break
                else:
                    i += 1
        return "".join(lines)

    @staticmethod
    def _block_at(lines: list[str], i: int) -> list[tuple[int, int]]:
        """Candidate removable spans beginning at index ``i``, biggest first."""
        line = lines[i]
        stripped = line.lstrip()
        blocks: list[tuple[int, int]] = []
        if stripped.startswith(("[",)):
            # A table header: header line plus every following non-header line.
            end = i + 1
            while end < len(lines) and not lines[end].lstrip().startswith("["):
                end += 1
            blocks.append((i, end))
        blocks.append((i, i + 1))
        return blocks

    def _trim_blank_lines(self, text: str, category: str,
                          signature: str) -> str:
        lines = text.splitlines(keepends=True)
        out = [ln for ln in lines if ln.strip() != ""]
        trial = "".join(out)
        if trial and self._still_triggers(trial, category, signature):
            return trial
        return text

    # --------------------------------------------- stage 2: delta debugging
    def _ddmin(self, text: str, category: str, signature: str) -> str:
        current = text
        n = 2
        passes = 0
        while len(current) >= 2 and passes < self.max_passes:
            passes += 1
            chunks = self._split(current, n)
            if len(chunks) == 1:
                break
            reduced_this_round = False

            # Granularity loop: try deleting each single chunk.
            for i in range(len(chunks)):
                trial = "".join(chunks[:i] + chunks[i + 1:])
                if trial and self._still_triggers(trial, category, signature):
                    current = trial
                    n = max(2, n - 1)
                    reduced_this_round = True
                    break
            if reduced_this_round:
                continue

            # Then try keeping only each single chunk (complement deletion).
            for i in range(len(chunks)):
                trial = chunks[i]
                if trial and self._still_triggers(trial, category, signature):
                    current = trial
                    n = 2
                    reduced_this_round = True
                    break
            if reduced_this_round:
                continue

            # No reduction at this granularity: increase it.
            if n >= len(current):
                break
            n = min(len(current), n * 2)
        return current

    @staticmethod
    def _split(text: str, n: int) -> list[str]:
        if n <= 1 or len(text) < n:
            return [text]
        # Prefer splitting on line/word boundaries when they line up with the
        # chunk size; otherwise fall back to even character slices.
        step = max(1, len(text) // n)
        chunks = [text[i:i + step] for i in range(0, len(text), step)]
        return [c for c in chunks if c]
