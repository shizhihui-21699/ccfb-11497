"""Run the official toml-test v1.0.0 corpus against tomlone.

* every ``tests/valid/**/*.toml`` must parse, and its typed-JSON encoding
  must equal the sibling ``.json`` fixture;
* every ``tests/invalid/**/*.toml`` must raise ``TOMLDecodeError``.

Usage:
    python tools/conformance/run_conformance.py [--corpus PATH] [--json OUT]
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from encoder import encode  # noqa: E402

from tomlone import TOMLDecodeError, loads  # noqa: E402

_FRACTION_RE = re.compile(r"\.(\d+)")


def _normalize_datetime(text: str) -> str:
    """Collapse fractional-second digits the way Go's time layouts do:
    trailing zeros are insignificant (".600000" == ".6")."""
    match = _FRACTION_RE.search(text)
    if not match:
        return text
    digits = match.group(1).rstrip("0")
    replacement = ("." + digits) if digits else ""
    return text[: match.start()] + replacement + text[match.end():]


def _compare_scalar(expected: dict, actual: dict, path: str) -> str | None:
    etype, evalue = expected["type"], expected["value"]
    atype, avalue = actual["type"], actual["value"]
    if etype != atype:
        return f"{path}: type mismatch expected {etype} got {atype}"
    if etype == "float":
        if evalue.endswith("nan") or avalue.endswith("nan"):
            if evalue != avalue:
                return f"{path}: NaN mismatch expected {evalue} got {avalue}"
            return None
        if float(evalue) != float(avalue):
            return f"{path}: float mismatch expected {evalue} got {avalue}"
        return None
    if etype in ("datetime", "datetime-local", "date-local", "time-local"):
        if _normalize_datetime(evalue) != _normalize_datetime(avalue):
            return f"{path}: datetime mismatch expected {evalue} got {avalue}"
        return None
    # bool / integer / string compare their encoded text exactly
    if evalue != avalue:
        return f"{path}: value mismatch expected {evalue!r} got {avalue!r}"
    return None


def _is_wrapper(node: object) -> bool:
    return (
        isinstance(node, dict)
        and set(node.keys()) == {"type", "value"}
        and not isinstance(node["value"], (dict, list))
    )


def _compare(expected: object, actual: object, path: str = "$") -> str | None:
    if _is_wrapper(expected) or _is_wrapper(actual):
        if not (_is_wrapper(expected) and _is_wrapper(actual)):
            return f"{path}: scalar/table shape mismatch expected {expected!r} got {actual!r}"
        return _compare_scalar(expected, actual, path)
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return f"{path}: expected table got {type(actual).__name__}"
        if set(expected) != set(actual):
            return f"{path}: key mismatch expected {sorted(expected)} got {sorted(actual)}"
        for key in expected:
            diff = _compare(expected[key], actual[key], f"{path}.{key}")
            if diff:
                return diff
        return None
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return f"{path}: expected array got {type(actual).__name__}"
        if len(expected) != len(actual):
            return f"{path}: array length {len(expected)} != {len(actual)}"
        for i, (e, a) in enumerate(zip(expected, actual)):
            diff = _compare(e, a, f"{path}[{i}]")
            if diff:
                return diff
        return None
    if expected != actual:
        return f"{path}: value mismatch expected {expected!r} got {actual!r}"
    return None


def _read_doc(toml_path: Path) -> str:
    """Read a corpus file as strict UTF-8 bytes."""
    return toml_path.read_bytes().decode("utf-8")


def run(corpus: Path) -> dict:
    valid_tomls = sorted((corpus / "valid").rglob("*.toml"))
    invalid_tomls = sorted((corpus / "invalid").rglob("*.toml"))
    failures: list[dict] = []
    passed = 0

    for toml_path in valid_tomls:
        rel = toml_path.relative_to(corpus)
        fixture = toml_path.with_suffix(".json")
        try:
            doc = _read_doc(toml_path)
            actual = encode(loads(doc))
            expected = json.loads(fixture.read_text(encoding="utf-8"))
            diff = _compare(expected, actual)
            if diff:
                failures.append({"case": str(rel), "kind": "valid-mismatch", "detail": diff,
                                 "actual": actual})
            else:
                passed += 1
        except UnicodeDecodeError as exc:
            failures.append({"case": str(rel), "kind": "valid-exception",
                             "detail": f"UnicodeDecodeError: {exc}"})
        except Exception as exc:  # noqa: BLE001 - conformance harness reports all
            failures.append({
                "case": str(rel),
                "kind": "valid-exception",
                "detail": f"{type(exc).__name__}: {exc}",
                "trace": traceback.format_exc(limit=3),
            })

    for toml_path in invalid_tomls:
        rel = toml_path.relative_to(corpus)
        try:
            doc = _read_doc(toml_path)
        except UnicodeDecodeError:
            # malformed UTF-8 is a legitimate rejection reason
            passed += 1
            continue
        try:
            loads(doc)
        except TOMLDecodeError:
            passed += 1
        except Exception as exc:  # noqa: BLE001
            failures.append({
                "case": str(rel),
                "kind": "wrong-exception",
                "detail": f"{type(exc).__name__}: {exc}",
            })
        else:
            failures.append({"case": str(rel), "kind": "invalid-accepted",
                             "detail": "document must be rejected"})

    total = len(valid_tomls) + len(invalid_tomls)
    return {
        "total": total,
        "passed": passed,
        "failed": len(failures),
        "valid_cases": len(valid_tomls),
        "invalid_cases": len(invalid_tomls),
        "failures": failures,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    default_corpus = ROOT / "third_party" / "toml-test" / "tests"
    ap.add_argument("--corpus", type=Path, default=default_corpus)
    ap.add_argument("--json", type=Path, default=None, help="write machine-readable report")
    args = ap.parse_args()

    report = run(args.corpus)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"toml-test v1.0.0  total={report['total']} "
          f"(valid={report['valid_cases']}, invalid={report['invalid_cases']})")
    print(f"passed={report['passed']}  failed={report['failed']}")
    for f in report["failures"][:50]:
        print(f"  [{f['kind']}] {f['case']}: {f['detail']}")
    if len(report["failures"]) > 50:
        print(f"  ... and {len(report['failures']) - 50} more (see --json report)")
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
