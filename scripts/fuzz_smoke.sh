#!/usr/bin/env bash
# Fast local fuzz smoke run (no wall clock limit, fixed iteration count).
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="${1:-reports/smoke}"
N="${FUZZ_ITERATIONS:-20000}"
rm -rf "$OUT"
.venv/bin/python -I fuzz/run_fuzz.py --iterations "$N" --seed 1 \
  --seeds tests/tomltest-data/valid --gen-seeds 128 \
  --out "$OUT" --flush-every 100
.venv/bin/python -I fuzz/report.py "$OUT" >/dev/null
echo "Report: $OUT/report.md"
