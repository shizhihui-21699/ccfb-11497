#!/usr/bin/env bash
# Run the official toml-test conformance gate (TOML 1.0) against ztoml.
set -euo pipefail
cd "$(dirname "$0")/.."
exec toml-test test -toml 1.0 -timeout 5s \
  -decoder=".venv/bin/python tests/tomltest_harness/decode.py" "$@"
