#!/usr/bin/env python3
"""toml-test decoder harness for ztoml.

Protocol (see `toml-test help test`):
* read TOML from stdin
* valid input  -> print the JSON description to stdout, exit 0
* invalid input -> print an error to stderr, exit 1
"""

import sys
from pathlib import Path

# Project root -> src layout; works regardless of the runner's cwd.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ztoml import TOMLDecodeError, loads, to_tomltest_json  # noqa: E402


def main() -> int:
    data = sys.stdin.buffer.read()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        print(f"invalid UTF-8: {exc}", file=sys.stderr)
        return 1
    try:
        parsed = loads(text)
    except TOMLDecodeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    sys.stdout.write(to_tomltest_json(parsed))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
