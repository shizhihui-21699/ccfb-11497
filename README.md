# ztoml — from-scratch TOML 1.0 parser + differential fuzzer

A self-contained, spec-strict TOML 1.0 parser written from scratch in Python,
plus a grammar-aware differential fuzzing harness that compares it against
the standard-library `tomllib` and a third-party parser (`rtoml`, with
`tomlkit` available as a fourth opinion).

The project was built to answer a concrete risk: an existing parser silently
interpreting several TOML spellings differently, allowing configuration to be
bypassed.  Every parser discrepancy is therefore triaged by **who is right
under TOML 1.0**, not by majority vote.

## Layout

```
src/ztoml/            the parser (no third-party dependencies)
  scanner.py          character-level lexer: position, strings, keys, WS/CR
  values.py           integers, floats, booleans, dates/times
  parser.py           documents, tables, arrays-of-tables, inline tables,
                      dotted keys and all redefinition/implicit-table rules
  __init__.py         loads/load + the toml-test JSON description encoder
fuzz/
  ir.py               a TOML document IR and its renderer
  generator.py        random-but-valid document generator (table-tree driven)
  mutator.py          grammar-aware IR mutations + lexical text mutations
  adapters.py         one common interface over ztoml/tomllib/rtoml/tomlkit,
                      including a canonical value form (NaN, signed zero, dates)
  differ.py           accept/reject + value-split detection, classification,
                      and the known-divergence filter
  minimizer.py        line-block reduction + character-level DDMin
  engine.py           the long-running fuzzing loop, dedup, checkpointing
  report.py           Markdown + JSON statistics report
tests/
  test_ztoml.py                 targeted unit / regression tests
  test_tomltest_vectors.py      all official vectors, no external binary needed
  tomltest_harness/decode.py    stdin→JSON adapter for the toml-test runner
  tomltest-data/                copy of the official toml-test 1.0 vectors
scripts/
  tomlttest.sh, fuzz_smoke.sh, fuzz_overnight.sh
```

## Conformance gate

The official runner is the authoritative gate and currently passes fully:

```
valid:   205 passed, 0 failed
invalid: 474 passed, 0 failed
```

```bash
scripts/tomltest.sh                 # requires the toml-test binary (brew install toml-test)
.venv/bin/python -m pytest tests/   # same vectors, binary-free, plus unit tests
```

## How the fuzzer works

### Input generation

* The generator builds an explicit **table tree** and renders it to TOML, so
  header paths, dotted keys and arrays-of-tables can never collide the way
  naive text assembly does.
* Every grammar construct is produced, with deliberate over-sampling of the
  awkward corners: nested AOTs, inline/dotted tables, multiline strings,
  temporal values, non-decimal integers and underscore spellings.
* A 20k-document generation run is accepted by **both** reference parsers
  with zero rejections and zero value mismatches, giving a clean baseline.

### Mutation strategy (chosen design)

Two layers, applied per case:

1. **IR-level structural mutations (≈60–70%)** operate on syntax units —
   replace/corrupt a scalar, retarget a dotted key path, toggle a table into
   an array-of-tables, drop/add/duplicate a pair, nest an array.  Corruption
   draws from a curated set of historically problematic spellings
   (leading-zero numbers, signed non-decimal ints, near-miss escapes,
   out-of-range dates).
2. **Text-level lexical mutations** hit the seams where parsers actually
   diverge: delimiter deletion/duplication, quote flipping, comment and
   newline/CR insertion, number-boundary tricks and raw control characters.

### Comparison and triage

Only two signals count: an **accept/reject split** or a **value split**
(crashes always count).  Error wording is ignored.  A canonical value tree
makes NaN compare equal to NaN, preserves signed zero, and compares dates by
their exact identity.

Splits caused by *known, defensible* parser behavior are classified as
`known-divergence` and counted separately, so they cannot drown out real
defects.  See [`fuzz/known_divergences.md`](fuzz/known_divergences.md); the
main families are TOML 1.0-vs-1.1 differences (optional seconds, `\e`/`\x`,
multi-line inline tables, inline-table trailing commas) and numeric overflow
policy.

### Minimization (chosen algorithm)

A two-stage reducer with a predicate cache:

1. **Greedy line/section-block reduction** — TOML is line-oriented, so whole
   sections and lines are removed largest-first while the discrepancy
   survives.
2. **Character-level delta debugging (DDMin)** — the classic Zeller/Hildebrandt
   n-way chunk reduction, which needs no grammar assumptions and converges
   toward a 1-minimal sample.

Every reduction must preserve the original finding's category **and the exact
parser accept/reject vector**, so minimization can never erase the bug and
leave a stale sample behind.  Findings are de-duplicated by a structural
signature, so the report describes bug *classes*, not thousands of duplicates.

### Running

```bash
# quick bounded smoke run + report
scripts/fuzz_smoke.sh reports/smoke

# overnight (default 8h), checkpoints stats/findings as it goes
scripts/fuzz_overnight.sh
FUZZ_DURATION=3600 scripts/fuzz_overnight.sh      # 1 hour
```

Outputs per run: `stats.json` (checkpointed counters),
`findings/findings.jsonl` (one record per unique signature), one standalone
minimized `.toml` per finding, and generated `report.md` / `report.json`.

## Bugs found and fixed in ztoml during bring-up

Differential fuzzing surfaced real defects that the official vectors alone did
not; all are covered by regression tests:

* A bare carriage return immediately after a multiline-string opener was
  accepted (it must be rejected; only LF/CRLF are legal).  This was the
  highest-severity class — an over-acceptance, the direction that enables
  parser-confusion / config-bypass.
* A local time with an offset (`01:02:03Z`) was accepted, though offsets
  belong only to offset datetimes.
* Leading-zero decimals/floats and signed hex/oct/bin integers needed stricter
  rejection.
* A UTF-8 BOM anywhere other than the start was handled inconsistently; a
  leading BOM is now accepted and any other BOM rejected.

After these fixes, long runs report **zero real ztoml findings**: every
remaining split is a documented, by-design divergence of a reference parser.
