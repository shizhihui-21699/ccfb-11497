"""Command-line entry point for the differential fuzzer.

Examples
--------
smoke test:
    python tools/fuzz/run_fuzz.py -n 500 --seed 7
overnight campaign:
    python tools/fuzz/run_fuzz.py -n 2000000 --seed 20261117 \
        --out out/fuzz-night --no-minimize-initial
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from fuzz.runner import FuzzConfig, Fuzzer  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="TOML differential fuzzer")
    ap.add_argument("-n", "--iterations", type=int, default=10_000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", type=Path, default=Path("out/fuzz"))
    ap.add_argument("--seed-corpus", type=Path, default=None,
                    help="extra .toml corpus directory used as mutation seeds")
    ap.add_argument("--generated-seeds", type=int, default=64)
    ap.add_argument("--max-passes", type=int, default=4)
    ap.add_argument("--stats-interval", type=int, default=2000)
    ap.add_argument("--no-minimize", action="store_true",
                    help="skip ddmin minimization (faster, raw reproducers)")
    args = ap.parse_args()

    config = FuzzConfig(
        iterations=args.iterations,
        seed=args.seed,
        minimize=not args.no_minimize,
        out_dir=args.out,
        seed_corpus_dir=args.seed_corpus,
        generate_seeds=args.generated_seeds,
        max_mutation_passes=args.max_passes,
        stats_interval=args.stats_interval,
    )
    fuzzer = Fuzzer(config)
    summary = fuzzer.run()
    print("=== fuzz summary ===")
    for key, value in summary.items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
