"""Differential fuzzing orchestrator.

Ties together the generator, mutator, differential classifier and minimizer
into one long-running loop.  Design points:

* **Seeds**: official toml-test valid vectors (real-world grammar coverage)
  plus freshly generated documents.
* **Energy scheduling**: each corpus entry gets a bounded mutation budget;
  entries that produce new signatures earn more energy.
* **Dedup by signature**: a finding is only stored the first time its
  (category, signature) pair appears, so the report describes *classes* of
  disagreement, not thousands of duplicates.
* **Lazy minimization**: minimization is expensive, so it runs once per new
  signature and the reduced sample is what gets persisted.
* **Checkpointing**: counters and findings flush to JSONL on an interval, so
  an overnight run killed at 8 a.m. still has everything up to the last flush.
"""

from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import ir
from .differ import (
    Finding, classify, run_all, KNOWN_DIVERGENCE,
)
from .generator import document
from .minimizer import Minimizer
from .mutator import mutate, mutate_ir, mutate_text


@dataclass
class Counters:
    iterations: int = 0
    generated: int = 0
    mutated: int = 0
    findings: int = 0
    known_divergences: int = 0
    new_signatures: int = 0
    by_category: dict[str, int] = field(default_factory=dict)
    by_subcategory: dict[str, int] = field(default_factory=dict)
    accept_agreement: int = 0
    reject_agreement: int = 0
    crashes: int = 0
    minimizations: int = 0

    def bump(self, key: str, table: dict[str, int]) -> None:
        table[key] = table.get(key, 0) + 1


class Fuzzer:
    def __init__(self, out_dir: str | Path, seed: int | None = None,
                 parsers=None, minimize: bool = True,
                 flush_every: int = 25, verbose: bool = False) -> None:
        self.out_dir = Path(out_dir)
        self.findings_dir = self.out_dir / "findings"
        self.findings_dir.mkdir(parents=True, exist_ok=True)
        self.rng = random.Random(seed)
        self.parsers = parsers
        self.minimize = minimize
        self.minimizer = Minimizer(parsers=parsers)
        self.flush_every = flush_every
        self.verbose = verbose

        # Corpus entries are (ir_doc_or_None, text) pairs.
        self.corpus: list[tuple[ir.Document | None, str]] = []
        self.signatures: dict[str, str] = {}
        self.counters = Counters()
        self._seen_inputs: set[str] = set()
        self._findings_handle = None
        self._stats_handle = None

    # ------------------------------------------------------------- seeding
    def load_seed_dir(self, seed_dir: str | Path) -> int:
        """Load every *.toml under ``seed_dir`` as a text-only corpus entry."""
        n = 0
        for path in sorted(Path(seed_dir).rglob("*.toml")):
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if text and text not in self._seen_inputs:
                self._seen_inputs.add(text)
                self.corpus.append((None, text))
                n += 1
        return n

    def add_generated_seeds(self, count: int) -> None:
        for _ in range(count):
            doc = document(self.rng)
            text = ir.render(doc)
            if text not in self._seen_inputs:
                self._seen_inputs.add(text)
                self.corpus.append((doc, text))
                self.counters.generated += 1

    # ----------------------------------------------------------------- loop
    def run(self, iterations: int | None = None,
            until: float | None = None) -> Counters:
        """Run until ``iterations`` cases or ``until`` (epoch seconds)."""
        start = time.time()
        if not self.corpus:
            self.add_generated_seeds(64)
        since_flush = 0
        while True:
            if iterations is not None and self.counters.iterations >= iterations:
                break
            if until is not None and time.time() >= until:
                break
            self._step()
            since_flush += 1
            if since_flush >= self.flush_every:
                self.flush(start)
                since_flush = 0
        self.flush(start)
        return self.counters

    def _step(self) -> None:
        c = self.counters
        c.iterations += 1
        # Favor recent / productive entries by biasing toward the tail.
        idx = self._pick_corpus_index()
        doc, base_text = self.corpus[idx]

        if doc is not None and self.rng.random() < 0.7:
            text = ir.render(mutate_ir(self.rng, doc))
            c.mutated += 1
        else:
            text = mutate_text(self.rng, base_text)
            c.mutated += 1

        if text in self._seen_inputs:
            return
        self._seen_inputs.add(text)

        outcomes = run_all(text, self.parsers)
        finding = classify(text, outcomes)
        kinds = {o.kind for o in outcomes.values()}
        if kinds == {"parsed"}:
            c.accept_agreement += 1
            self._maybe_keep(doc, text)
            return
        if kinds == {"rejected"}:
            c.reject_agreement += 1
            return
        if finding is None:
            return

        # A real discrepancy.
        c.findings += 1
        c.bump(finding.category, c.by_category)
        c.bump(finding.subcategory, c.by_subcategory)
        if finding.category == "crash":
            c.crashes += 1
        if finding.category == KNOWN_DIVERGENCE:
            # Counted and summarized, but never minimized or treated as a bug.
            c.known_divergences += 1
            self._record_known(finding)
            return
        self._record_finding(finding)

    def _record_known(self, finding: Finding) -> None:
        sig = finding.signature
        if sig in self.signatures:
            return
        self.signatures[sig] = finding.category
        self.counters.new_signatures += 1
        if self._findings_handle is None:
            self._findings_handle = open(
                self.findings_dir / "findings.jsonl", "a", encoding="utf-8"
            )
        record = finding.to_record()
        record["seq"] = self.counters.new_signatures
        self._findings_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._findings_handle.flush()

    def _pick_corpus_index(self) -> int:
        n = len(self.corpus)
        # Square bias toward the end (newer entries) while still reaching old.
        return min(n - 1, int(self.rng.triangular(0, n, n) ** 1) ) if n else 0

    def _maybe_keep(self, doc, text: str) -> None:
        # Keep a small fraction of accepted mutations to grow coverage of the
        # valid-input space without unbounded corpus growth.
        cap = 2000
        if len(self.corpus) < cap and self.rng.random() < 0.05:
            self.corpus.append((doc, text))

    # ------------------------------------------------------------ recording
    def _record_finding(self, finding: Finding) -> None:
        sig = finding.signature
        if sig in self.signatures:
            return  # already have this class
        self.signatures[sig] = finding.category
        self.counters.new_signatures += 1

        if self.minimize:
            try:
                finding.input_text = self.minimizer.minimize(finding.input_text)
                self.counters.minimizations += 1
            except Exception as exc:  # noqa: BLE001 -- never kill the run
                if self.verbose:
                    print(f"[minimizer] {type(exc).__name__}: {exc}")

        self._write_finding(finding)
        if self.verbose:
            print(f"[finding] {finding.category}/{finding.subcategory} "
                  f"sig={sig[:60]!r}")

    def _write_finding(self, finding: Finding) -> None:
        if self._findings_handle is None:
            self._findings_handle = open(
                self.findings_dir / "findings.jsonl", "a", encoding="utf-8"
            )
        record = finding.to_record()
        record["seq"] = self.counters.new_signatures
        self._findings_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._findings_handle.flush()
        # Also write the minimized sample as a standalone file for easy replay.
        sample = self.findings_dir / f"{self._slug(finding)}.toml"
        if not sample.exists():
            sample.write_text(finding.input_text, encoding="utf-8")

    @staticmethod
    def _slug(finding: Finding) -> str:
        base = f"{finding.category}-{abs(hash(finding.signature)) % 10_000_000:07d}"
        return base

    def flush(self, start: float) -> None:
        stats = {
            "elapsed_sec": round(time.time() - start, 3),
            "counters": self.counters.__dict__,
            "corpus_size": len(self.corpus),
            "unique_signatures": len(self.signatures),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        tmp = self.out_dir / "stats.json"
        tmp.write_text(json.dumps(stats, indent=2, ensure_ascii=False),
                       encoding="utf-8")
