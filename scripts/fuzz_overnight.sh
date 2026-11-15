#!/usr/bin/env bash
# Overnight differential fuzzing.
#
# Defaults to an 8-hour wall-clock run seeded with the official valid
# vectors. Override with FUZZ_DURATION=<seconds> and FUZZ_ITERATIONS=<n>.
set -euo pipefail
cd "$(dirname "$0")/.."

DURATION="${FUZZ_DURATION:-28800}"
ITERATIONS="${FUZZ_ITERATIONS:-}"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="reports/night-${STAMP}"
mkdir -p "$OUT"

ARGS=(--duration "$DURATION" --seeds tests/tomltest-data/valid
      --gen-seeds 256 --out "$OUT" --flush-every 200 -v)
if [[ -n "$ITERATIONS" ]]; then
  ARGS=(--iterations "$ITERATIONS" --seeds tests/tomltest-data/valid
        --gen-seeds 256 --out "$OUT" --flush-every 200 -v)
fi

# -I keeps the interpreter isolated from cwd sys.path planting.
.venv/bin/python -I fuzz/run_fuzz.py "${ARGS[@]}"
.venv/bin/python -I fuzz/report.py "$OUT" >/dev/null
echo "Report: $OUT/report.md"
