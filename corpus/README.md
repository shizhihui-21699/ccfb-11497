# Corpus

Working directory for fuzz inputs.

* The fuzzer seeds itself from the official vectors in
  `../tests/tomltest-data/valid` plus freshly generated documents, so this
  directory does not need to be populated by hand.
* Minimized, reproducible findings are written to
  `<run-dir>/findings/` (see `../reports/`), one `.toml` per unique
  signature alongside `findings.jsonl`.
