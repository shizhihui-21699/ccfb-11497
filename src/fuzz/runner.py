"""Differential fuzzing orchestrator.

Loop
----
1. maintain a seed corpus (generated valid docs + any on-disk corpus);
2. each iteration pick a seed, apply grammar-aware mutation(s);
3. run every engine under exception isolation;
4. classify the outcome with the differential oracle;
5. on a non-consensus verdict, dedup by stable signature and minimize the
   reproducer with layered ddmin;
6. record statistics and persist findings incrementally.

The runner is deterministic for a given seed and writes findings as they
arrive, so an overnight run can be interrupted without losing results.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import random
import time
from collections import Counter, defaultdict
from pathlib import Path

from .engines import STRICT_1_0_REFERENCE, available_engines, run_engine
from .generator import generate
from .minimizer import discrepancy_predicate, minimize
from .mutators import MUTATORS, mutate
from .oracle import classify


@dataclasses.dataclass
class FuzzConfig:
    iterations: int = 10_000
    seed: int = 1
    minimize: bool = True
    out_dir: Path = Path("out/fuzz")
    seed_corpus_dir: Path | None = None
    generate_seeds: int = 64
    max_mutation_passes: int = 4
    stats_interval: int = 2_000


class Fuzzer:
    def __init__(self, config: FuzzConfig) -> None:
        self.cfg = config
        self.rng = random.Random(config.seed)
        self.engines = available_engines()
        self.engine_names = sorted(self.engines)
        self.seeds: list[str] = []
        # signature -> finding record (deduplicates the same root cause)
        self.findings: dict[str, dict] = {}
        self.stats = Counter()
        self.kind_counts = Counter()
        self.strategy_counts = Counter()
        self.strategy_hit_counts = Counter()
        self.start_time = 0.0
        self._eval_budget = 0

    # -- setup --------------------------------------------------------------

    def build_seeds(self) -> None:
        gen_rng = random.Random(self.cfg.seed * 7919 + 13)
        for _ in range(self.cfg.generate_seeds):
            self.seeds.append(generate(gen_rng))
        if self.cfg.seed_corpus_dir:
            for path in sorted(self.cfg.seed_corpus_dir.rglob("*.toml")):
                try:
                    self.seeds.append(path.read_text(encoding="utf-8",
                                                      errors="replace"))
                except OSError:
                    continue

    # -- engine execution ---------------------------------------------------

    def run_all(self, text: str):
        self._eval_budget += 1
        results = []
        for name in self.engine_names:
            results.append(run_engine(name, self.engines[name], text))
        return results

    # -- main loop ----------------------------------------------------------

    def run(self) -> dict:
        self.build_seeds()
        self.cfg.out_dir.mkdir(parents=True, exist_ok=True)
        cases_dir = self.cfg.out_dir / "cases"
        cases_dir.mkdir(exist_ok=True)
        self.start_time = time.monotonic()

        for iteration in range(self.cfg.iterations):
            seed = self.rng.choice(self.seeds)
            mutated, strategies = mutate(
                seed, self.rng, self.cfg.max_mutation_passes
            )
            self.strategy_counts.update(strategies)
            self.stats["iterations"] += 1

            results = self.run_all(mutated)
            verdict = classify(results)
            self.kind_counts[verdict.kind] += 1

            if verdict.kind not in ("consensus", "precision"):
                self.strategy_hit_counts.update(strategies)
                self._record_finding(verdict, mutated, strategies,
                                     results, cases_dir)
            elif verdict.kind == "precision":
                self.stats["precision-notes"] += 1

            if (iteration + 1) % self.cfg.stats_interval == 0:
                self._write_stats()

        self._write_stats()
        self._write_findings_index()
        return self.summary()

    # -- finding handling ---------------------------------------------------

    def _record_finding(self, verdict, text, strategies, results,
                        cases_dir: Path) -> None:
        sig = verdict.signature
        if sig in self.findings:
            self.findings[sig]["occurrences"] += 1
            return

        minimized = text
        reduction = 0.0
        if self.cfg.minimize:
            pred = discrepancy_predicate(self.run_all, sig)
            try:
                minimized = minimize(text, pred)
                reduction = 1.0 - (len(minimized) / max(len(text), 1))
            except Exception as exc:  # noqa: BLE001
                self.stats["minimize-errors"] += 1
                minimized = text

        digest = hashlib.sha256(sig.encode()).hexdigest()[:12]
        case_path = cases_dir / f"{digest}.toml"
        case_path.write_text(minimized, encoding="utf-8")

        # which engine diverged from the majority, framed for triage
        accept = sorted(r.name for r in results if r.ok)
        reject = sorted(r.name for r in results if not r.ok)
        record = {
            "signature": sig,
            "kind": verdict.kind,
            "detail": verdict.detail,
            "category": self._category(verdict),
            "accepted_by": accept,
            "rejected_by": reject,
            "reference_expected": verdict.reference_ok,
            "strategies": strategies,
            "occurrences": 1,
            "original_size": len(text),
            "minimized_size": len(minimized),
            "size_reduction": round(reduction, 4),
            "case_file": str(case_path.relative_to(self.cfg.out_dir)),
            "engine_errors": {
                r.name: r.error_type for r in results if not r.ok
            },
            "crashes": {r.name: r.crash for r in results if r.crash},
        }
        self.findings[sig] = record
        self._write_findings_index()  # durable after every new signature

    @staticmethod
    def _category(verdict) -> str:
        if verdict.kind == "crash":
            engines = verdict.detail
            return "engine-crash"
        if verdict.kind == "accept-split":
            our = verdict.results.get("tomlone")
            strict = verdict.results.get(STRICT_1_0_REFERENCE)
            # Judge tomlone against the strict TOML 1.0 reference rather than
            # a raw majority that includes TOML-1.1 engines. Disagreeing with
            # the strict engine is a real 1.0 defect; agreeing with it while a
            # 1.1 engine differs is just a version split.
            if our is not None and strict is not None:
                if our.ok and not strict.ok:
                    return "tomlone-false-accept (bypass risk)"
                if not our.ok and strict.ok:
                    return "tomlone-false-reject"
            # fallback to majority only when the strict reference is absent
            if our is not None and our.ok and verdict.reference_ok is False:
                return "tomlone-false-accept (bypass risk)"
            if our is not None and not our.ok and verdict.reference_ok is True:
                return "tomlone-false-reject"
            return "accept-split-other"
        if verdict.kind == "value-split":
            our = verdict.results.get("tomlone")
            strict = verdict.results.get(STRICT_1_0_REFERENCE)
            # A value divergence is a tomlone defect only if we disagree with
            # the strict-1.0 reference; otherwise it is a third-party engine
            # producing a different (often 1.1-flavoured) value.
            if our is not None and strict is not None and strict.ok:
                if our.canonical != strict.canonical:
                    return "tomlone-value-divergence"
                return "third-party-value-divergence"
            others = [r for n, r in verdict.results.items() if n != "tomlone"]
            if our is not None and all(
                r.canonical != our.canonical for r in others if r.ok
            ):
                return "tomlone-value-divergence"
            return "third-party-value-divergence"
        return verdict.kind

    # -- reporting ----------------------------------------------------------

    def _write_stats(self) -> None:
        elapsed = time.monotonic() - self.start_time
        iters = self.stats["iterations"]
        strategy_yield = {}
        for name in self.strategy_counts:
            total = self.strategy_counts[name]
            hits = self.strategy_hit_counts[name]
            strategy_yield[name] = {
                "uses": total,
                "disagreements": hits,
                "yield_per_1k": round(1000 * hits / total, 3) if total else 0.0,
                "weight": MUTATORS[name][1],
            }
        payload = {
            "elapsed_seconds": round(elapsed, 2),
            "iterations": iters,
            "engine_evaluations": self._eval_budget,
            "iterations_per_second": round(iters / elapsed, 1) if elapsed else 0,
            "outcome_counts": dict(self.kind_counts),
            "unique_findings": len(self.findings),
            "finding_categories": dict(Counter(
                f["category"] for f in self.findings.values()
            )),
            "strategy_stats": strategy_yield,
            "counters": dict(self.stats),
        }
        (self.cfg.out_dir / "stats.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )

    def _write_findings_index(self) -> None:
        records = sorted(
            self.findings.values(),
            key=lambda r: (r["kind"], r["signature"]),
        )
        (self.cfg.out_dir / "findings.json").write_text(
            json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def summary(self) -> dict:
        return {
            "iterations": self.stats["iterations"],
            "outcomes": dict(self.kind_counts),
            "unique_findings": len(self.findings),
            "categories": dict(Counter(
                f["category"] for f in self.findings.values()
            )),
            "out_dir": str(self.cfg.out_dir),
        }
