"""Turn a fuzz campaign directory into a human-readable Markdown report.

Reads ``stats.json`` and ``findings.json`` written by the runner and emits a
report covering: throughput, outcome distribution, finding categories with
triage framing, per-strategy yield ranking, and every minimized reproducer
inlined so the report is self-contained.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CATEGORY_EXPLAIN = {
    "tomlone-false-accept (bypass risk)":
        "Our parser ACCEPTS input the references reject — the highest "
        "priority class, the original 'config bypass' shape.",
    "tomlone-false-reject":
        "Our parser rejects input the references accept — compatibility bug.",
    "tomlone-value-divergence":
        "All accept, but our decoded value differs from every reference.",
    "third-party-value-divergence":
        "All accept, but one reference engine decodes a different value.",
    "accept-split-other":
        "Engines split on accept/reject with no majority; inspect manually.",
    "engine-crash":
        "An engine raised a non-parser exception (panic/assert/IndexError).",
}


def load(run_dir: Path) -> tuple[dict, list[dict]]:
    stats = json.loads((run_dir / "stats.json").read_text(encoding="utf-8"))
    findings_path = run_dir / "findings.json"
    findings = (
        json.loads(findings_path.read_text(encoding="utf-8"))
        if findings_path.exists() else []
    )
    return stats, findings


def render(run_dir: Path) -> str:
    stats, findings = load(run_dir)
    out: list[str] = []
    out.append("# TOML Differential Fuzz Report\n")
    out.append(f"Run directory: `{run_dir}`\n")

    out.append("## Throughput\n")
    out.append(f"- iterations: **{stats['iterations']:,}**")
    out.append(f"- engine evaluations: **{stats['engine_evaluations']:,}** "
               f"({len(stats['outcome_counts']) and '4 engines/iter'})")
    out.append(f"- elapsed: {stats['elapsed_seconds']:,.1f} s")
    out.append(f"- throughput: **{stats['iterations_per_second']:,.0f} iter/s**\n")

    out.append("## Outcome distribution\n")
    outcomes = stats["outcome_counts"]
    total = stats["iterations"] or 1
    for kind in ("consensus", "precision", "accept-split", "value-split", "crash"):
        count = outcomes.get(kind, 0)
        out.append(f"- `{kind}`: {count:,} ({100 * count / total:.2f}%)")
    out.append("")

    out.append("## Finding categories\n")
    cats = stats.get("finding_categories", {})
    if not cats:
        out.append("No disagreements found — all engines agreed.\n")
    for cat, count in sorted(cats.items(), key=lambda kv: -kv[1]):
        out.append(f"- **{cat}**: {count} unique signature(s)")
        explain = CATEGORY_EXPLAIN.get(cat)
        if explain:
            out.append(f"  - {explain}")
    out.append("")

    out.append("## Mutation strategy yield\n")
    out.append("Strategies ranked by disagreements per 1,000 uses.\n")
    rows = sorted(
        stats.get("strategy_stats", {}).items(),
        key=lambda kv: (-kv[1]["yield_per_1k"], -kv[1]["uses"]),
    )
    out.append("| strategy | uses | disagreements | yield/1k | weight |")
    out.append("|---|---:|---:|---:|---:|")
    for name, s in rows:
        out.append(
            f"| `{name}` | {s['uses']:,} | {s['disagreements']:,} | "
            f"{s['yield_per_1k']:.3f} | {s['weight']} |"
        )
    out.append("")

    out.append(f"## Minimized reproducers ({len(findings)})\n")
    for i, f in enumerate(findings, 1):
        out.append(f"### {i}. [{f['kind']}] `{f['category']}`")
        out.append(f"- signature: `{f['signature']}`")
        out.append(f"- occurrences: {f['occurrences']:,}")
        out.append(f"- accepted by: {', '.join(f['accepted_by']) or '—'}")
        out.append(f"- rejected by: {', '.join(f['rejected_by']) or '—'}")
        if f.get("crashes"):
            out.append(f"- crashes: `{json.dumps(f['crashes'], ensure_ascii=False)}`")
        out.append(f"- size: {f['original_size']} → {f['minimized_size']} bytes "
                   f"({100 * f['size_reduction']:.1f}% reduction)")
        out.append(f"- strategies: {', '.join(f['strategies'])}")
        out.append("")
        case_path = run_dir / f["case_file"]
        if case_path.exists():
            body = case_path.read_text(encoding="utf-8", errors="replace")
            out.append("```toml")
            out.append(body.rstrip("\n"))
            out.append("```")
        out.append("")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("-o", "--output", type=Path, default=None)
    args = ap.parse_args()
    report = render(args.run_dir)
    if args.output:
        args.output.write_text(report, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
