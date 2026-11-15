"""Turn a fuzz run directory into a human-readable statistics report.

Reads ``stats.json`` and ``findings/findings.jsonl`` produced by the engine and
emits both a Markdown report (for reading) and a structured JSON summary (for
machine processing / trend tracking across nightly runs).
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def load_run(run_dir: Path) -> tuple[dict, list[dict]]:
    stats = json.loads((run_dir / "stats.json").read_text(encoding="utf-8"))
    findings_path = run_dir / "findings" / "findings.jsonl"
    findings = []
    if findings_path.exists():
        for line in findings_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                findings.append(json.loads(line))
    return stats, findings


def summarize(stats: dict, findings: list[dict]) -> dict:
    counters = stats.get("counters", {})
    iters = max(1, counters.get("iterations", 1))
    elapsed = stats.get("elapsed_sec", 0.0)

    real = [f for f in findings if f["category"] != "known-divergence"]
    known = [f for f in findings if f["category"] == "known-divergence"]

    label_counter: Counter = Counter()
    for f in real:
        label_counter.update(f.get("labels", []))

    blame_counter = Counter(f["subcategory"] for f in real)
    minority_counter = Counter()
    for f in real:
        minority_counter.update(f.get("minority", []))

    # Findings whose minority view is held solely by ztoml are the priority:
    # they are either a security-relevant over-acceptance or an over-strict bug.
    ztoml_solo = [
        f for f in real if f.get("minority") == ["ztoml"]
    ]

    return {
        "run_timestamp": stats.get("timestamp"),
        "elapsed_sec": round(elapsed, 2),
        "throughput_per_sec": round(iters / elapsed, 1) if elapsed else None,
        "iterations": counters.get("iterations", 0),
        "corpus_size": stats.get("corpus_size"),
        "unique_signatures": stats.get("unique_signatures"),
        "agreement": {
            "all_accept": counters.get("accept_agreement", 0),
            "all_reject": counters.get("reject_agreement", 0),
            "agreement_rate": round(
                (counters.get("accept_agreement", 0)
                 + counters.get("reject_agreement", 0)) / iters, 5
            ),
        },
        "findings_total_raw": counters.get("findings", 0),
        "findings_real": len(real),
        "findings_known_divergence": len(known),
        "crashes": counters.get("crashes", 0),
        "minimizations": counters.get("minimizations", 0),
        "by_category": counters.get("by_category", {}),
        "blame": dict(blame_counter.most_common()),
        "minority_parsers": dict(minority_counter.most_common()),
        "ztoml_solo_findings": len(ztoml_solo),
        "top_grammar_labels": dict(label_counter.most_common(12)),
        "known_divergence_kinds": dict(
            Counter(f["subcategory"] for f in known).most_common()
        ),
    }


def render_markdown(summary: dict, findings: list[dict]) -> str:
    s = summary
    lines = [
        f"# Differential fuzz report",
        "",
        f"- Generated: {s['run_timestamp']}",
        f"- Wall time: {s['elapsed_sec']} s "
        f"({s['throughput_per_sec']} cases/sec)",
        f"- Iterations: {s['iterations']:,}",
        f"- Corpus size: {s['corpus_size']}",
        "",
        "## Agreement",
        "",
        f"- All parsers accept: {s['agreement']['all_accept']:,}",
        f"- All parsers reject: {s['agreement']['all_reject']:,}",
        f"- Agreement rate: {s['agreement']['agreement_rate']:.3%}",
        "",
        "## Findings",
        "",
        f"- Raw discrepancy events: {s['findings_total_raw']:,}",
        f"- Unique real findings: **{s['findings_real']}**",
        f"- Known (by-design) divergences: {s['findings_known_divergence']}",
        f"- Crashes: **{s['crashes']}**",
        f"- Cases where ztoml alone holds the minority view: "
        f"**{s['ztoml_solo_findings']}**",
        f"- Minimizations run: {s['minimizations']}",
        "",
        "### By category",
        "",
        "| category | count |",
        "|---|---|",
    ]
    for k, v in s["by_category"].items():
        lines.append(f"| {k} | {v} |")

    lines += ["", "### Blame (real findings only)", "",
              "| subcategory | count |", "|---|---|"]
    for k, v in s["blame"].items():
        lines.append(f"| {k} | {v} |")

    lines += ["", "### Grammar labels on real findings", "",
              "| label | count |", "|---|---|"]
    for k, v in s["top_grammar_labels"].items():
        lines.append(f"| {k} | {v} |")

    if s["known_divergence_kinds"]:
        lines += ["", "### Known divergences (suppressed, not bugs)", "",
                  "| kind | count |", "|---|---|"]
        for k, v in s["known_divergence_kinds"].items():
            lines.append(f"| {k} | {v} |")

    real = [f for f in findings if f["category"] != "known-divergence"]
    if real:
        lines += ["", "## Real finding samples (minimized)", ""]
        for f in real:
            lines.append(f"### `{f['category']}/{f['subcategory']}` "
                         f"— {f['signature'][:70]}")
            lines.append("")
            lines.append("```toml")
            lines.append(f["input"].rstrip("\n"))
            lines.append("```")
            lines.append("")
            lines.append("| parser | result | detail |")
            lines.append("|---|---|---|")
            for name, o in f["outcomes"].items():
                detail = (o.get("detail") or str(o.get("payload", "")))[:80]
                lines.append(f"| {name} | {o['kind']} | {detail} |")
            lines.append("")
    return "\n".join(lines)


def write_reports(run_dir: str | Path) -> dict:
    run_dir = Path(run_dir)
    stats, findings = load_run(run_dir)
    summary = summarize(stats, findings)
    (run_dir / "report.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (run_dir / "report.md").write_text(
        render_markdown(summary, findings), encoding="utf-8"
    )
    return summary


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Build a fuzz statistics report")
    p.add_argument("run_dir", help="fuzzer output directory")
    args = p.parse_args(argv)
    summary = write_reports(args.run_dir)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
