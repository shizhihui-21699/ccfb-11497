#!/usr/bin/env python3
"""Command-line entry point for the ztoml differential fuzzer.

Examples
--------
# 20k-iteration smoke run
python -m fuzz.run_fuzz --iterations 20000 --out reports/run-smoke

# Overnight wall-clock run (8 hours), official vectors seeded in
python -m fuzz.run_fuzz --duration 28800 \
    --seeds tests/tomltest-data/valid --out reports/run-night
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Allow running both as `python -m fuzz.run_fuzz` and as a direct script,
# including under isolated mode (python -I), where cwd/PYTHONPATH are ignored.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fuzz.adapters import ALL_PARSERS, TRIO  # noqa: E402
from fuzz.engine import Fuzzer  # noqa: E402


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="ztoml differential fuzzer")
    p.add_argument("--iterations", type=int, default=None,
                   help="maximum number of fuzz cases")
    p.add_argument("--duration", type=float, default=None,
                   help="maximum wall-clock time in seconds")
    p.add_argument("--seeds", action="append", default=[],
                   help="directory of *.toml seed files (repeatable)")
    p.add_argument("--gen-seeds", type=int, default=64,
                   help="number of generated seed documents (default 64)")
    p.add_argument("--out", required=True, help="output directory")
    p.add_argument("--seed", type=int, default=None, help="RNG seed")
    p.add_argument("--no-minimize", action="store_true",
                   help="disable input minimization")
    p.add_argument("--all-parsers", action="store_true",
                   help="include tomlkit in addition to the core trio")
    p.add_argument("--flush-every", type=int, default=25,
                   help="flush stats every N cases (default 25)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    parsers = ALL_PARSERS if args.all_parsers else TRIO
    fuzzer = Fuzzer(
        out_dir=args.out,
        seed=args.seed,
        parsers=parsers,
        minimize=not args.no_minimize,
        flush_every=args.flush_every,
        verbose=args.verbose,
    )

    loaded = 0
    for seed_dir in args.seeds:
        loaded += fuzzer.load_seed_dir(seed_dir)
    fuzzer.add_generated_seeds(args.gen_seeds)
    print(f"[fuzz] corpus seeds: {loaded} from files + "
          f"{args.gen_seeds} generated = {len(fuzzer.corpus)} total",
          file=sys.stderr)
    print(f"[fuzz] parsers: {', '.join(parsers)}", file=sys.stderr)

    until = time.time() + args.duration if args.duration else None
    counters = fuzzer.run(iterations=args.iterations, until=until)

    print("\n=== fuzz summary ===")
    print(f"iterations         : {counters.iterations}")
    print(f"  generated seeds  : {counters.generated}")
    print(f"  mutations        : {counters.mutated}")
    print(f"all-accept agree   : {counters.accept_agreement}")
    print(f"all-reject agree   : {counters.reject_agreement}")
    print(f"findings (raw)     : {counters.findings}")
    print(f"  known divergences: {counters.known_divergences}")
    print(f"unique signatures  : {counters.new_signatures}")
    print(f"minimizations      : {counters.minimizations}")
    print(f"by category        : {counters.by_category}")
    print(f"by subcategory     : {counters.by_subcategory}")
    print(f"findings written to: {Path(args.out) / 'findings'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
