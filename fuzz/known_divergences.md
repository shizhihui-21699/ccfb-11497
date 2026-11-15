# Known cross-parser divergences

These are inputs where the parsers disagree **by design**, not because of a
ztoml bug.  The fuzzer suppresses findings that match one of these patterns so
the report stays focused on genuine ztoml defects.  Each entry records who
diverges and why, with the governing spec citation.

## 1. Optional seconds in a time (`HH:MM` without `:SS`)

* **Spec**: TOML 1.0 ABNF makes `second` mandatory in `partial-time`.
  Omitting seconds is a **TOML 1.1** addition.
* **Behavior**: CPython `tomllib` already implements the 1.1 rule and accepts
  `1987-07-05T17:45` / `17:45`; `ztoml` and the official toml-test 1.0 vectors
  reject them.
* **Verdict**: ztoml is correct for the 1.0 target.  Tracked, not a bug.
* **Match**: a temporal token matching `\d{2}:\d{2}` not followed by `:`.

## 2. Numeric overflow (`1e9999`, huge integers)

* **Spec**: TOML 1.0 does not define float-overflow behavior, and its
  toml-test suite states that int64 support "is not mandatory".
* **Behavior**: `rtoml` (Rust) hard-rejects both float overflow
  (`floating-point number overflowed`) and integers beyond i64
  (`integer number overflowed`).  `tomllib` and `ztoml` map overflowing
  floats to `inf` and keep integers at Python arbitrary precision.
* **Verdict**: implementation choice; both readings are conforming.
* **Match**: a large exponent literal, or an integer spelling beyond i64.

## 3. TOML 1.1-only escape sequences (`\e`, `\x`)

* **Spec**: TOML 1.0's escape set is `b t n f r " u U \`; `\e` (ESC) and
  `\x..` are **TOML 1.1** additions.
* **Behavior**: `tomllib`/`rtoml` (both 1.1-aware) accept them; `ztoml`
  rejects them, matching the official toml-test 1.0 bad-escape vectors.
* **Verdict**: ztoml is correct for the 1.0 target.

## 4. Signed NaN payload

* **Spec**: TOML permits `nan`, `+nan`, `-nan`; the sign of NaN has no
  portable meaning.
* **Behavior**: all three parse it, but the exact float bit pattern is not
  guaranteed to compare equal.  Canonical comparison folds every NaN to one
  tag, so this should never produce a value-split; listed for completeness.

## 5. Newlines inside inline tables

* **Spec**: TOML 1.0 requires an inline table to appear on a single line; the
  only newlines permitted between its braces are those valid inside a value
  (e.g. a multiline string).  TOML 1.1 relaxes this and allows multi-line
  inline tables.
* **Behavior**: `ztoml` rejects a structural linebreak inside `{ ... }`
  (matching the four official `invalid/inline-table/linebreak-*` 1.0 vectors);
  `tomllib` follows 1.1 and accepts them.  `rtoml` is inconsistent and also
  accepts several such cases.
* **Verdict**: ztoml is correct for the 1.0 target.

## 6. Trailing comma inside an inline table

* **Spec**: TOML 1.0 forbids a comma after the last key/value pair of an
  inline table (`{a=1,}`); TOML 1.1 permits it.  Note arrays allow a trailing
  comma in both versions, so this applies only between `{ }`.
* **Behavior**: `ztoml` rejects it (matching
  `invalid/inline-table/trailing-comma.toml`); the 1.1-aware references
  accept it.
* **Verdict**: ztoml is correct for the 1.0 target.

## 7. CRLF normalization inside multiline strings

* **Spec**: TOML 1.0 requires CRLF *and* bare CR inside multiline strings to
  be normalized to LF.
* **Behavior**: `ztoml` and `tomllib` normalize to LF; `rtoml` preserves the
  CR, producing a different (spec-non-conforming) value.
* **Verdict**: an rtoml defect; ztoml conforms.  Surfaced as a value-split.

## 8. Error message text and column numbers

* Rejections are compared only on accept/reject *status*, never on message
  wording, since each implementation phrases diagnostics differently.
