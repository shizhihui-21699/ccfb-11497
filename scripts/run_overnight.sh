#!/usr/bin/env bash
# Overnight differential-fuzzing campaign.
#
# Runs a long, self-contained fuzz campaign into a timestamped directory,
# then renders the human-readable Markdown report. All output is durable:
# findings and stats are rewritten incrementally, so killing this script
# loses nothing already discovered.
#
# Usage:
#   scripts/run_overnight.sh [ITERATIONS] [SEED]
# Example (all-night, ~8h at ~3k iter/s):
#   scripts/run_overnight.sh 80000000 20261117
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="$ROOT/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "venv missing — run: python3 -m venv .venv && .venv/bin/pip install -e '.[fuzz]' pytest" >&2
  exit 1
fi

# Default 100M iterations ≈ 5.5h at the observed ~5k iter/s; override with $1.
ITERATIONS="${1:-100000000}"
SEED="${2:-$(date +%Y%m%d)}"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$ROOT/out/night-$STAMP"
CORPUS="$ROOT/third_party/toml-test/tests/valid"

echo "campaign: iterations=$ITERATIONS seed=$SEED out=$OUT"
"$PY" -I tools/fuzz/run_fuzz.py \
  -n "$ITERATIONS" \
  --seed "$SEED" \
  --out "$OUT" \
  --seed-corpus "$CORPUS" \
  --stats-interval 25000

"$PY" -I tools/fuzz/report.py "$OUT" -o "$OUT/REPORT.md"
echo "report written to $OUT/REPORT.md"

# Compact triage summary so a scheduled/CI run surfaces any tomlone defect
# in its exit log. Third-party-only categories do not fail the run.
"$PY" - "$OUT" <<'PYEOF'
import json, sys
out = sys.argv[1]
stats = json.load(open(f"{out}/stats.json"))
cats = stats.get("finding_categories", {})
own = {k: v for k, v in cats.items() if "tomlone" in k}
print("--- campaign summary ---")
print(f"iterations={stats['iterations']:,} rate={stats['iterations_per_second']}/s "
      f"unique_findings={stats['unique_findings']}")
print("categories:", json.dumps(cats, ensure_ascii=False))
if own:
    print("TOMLONE-SPECIFIC DISAGREEMENTS FOUND:", own)
    sys.exit(2)
print("no tomlone-specific disagreement — remaining findings are third-party only")
PYEOF
