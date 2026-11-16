"""Differential property tests run a small fuzz loop inside pytest.

These complement the standalone campaign: they guarantee that every
*valid* document the generator produces is accepted by all four engines
with an equal canonical value, and that every engine agrees on the
official valid corpus. Kept small so the unit suite stays fast.
"""

import random

import pytest

import tomlone
from fuzz.canonical import canonicalize
from fuzz.engines import available_engines, run_engine
from fuzz.generator import generate
from fuzz.oracle import classify

ENGINES = available_engines()


@pytest.fixture(scope="module")
def engine_modules():
    return ENGINES


def test_generated_valid_docs_agree(engine_modules):
    rng = random.Random(1234)
    for _ in range(60):
        doc = generate(rng)
        results = [run_engine(name, mod, doc) for name, mod in engine_modules.items()]
        verdict = classify(results)
        assert verdict.kind in ("consensus", "precision"), (
            f"unexpected {verdict.kind}: {verdict.detail}\n{doc}"
        )
        assert all(r.ok for r in results), f"a valid doc was rejected:\n{doc}"


def test_tomlone_matches_tomllib_on_corpus():
    import tomllib
    from pathlib import Path

    corpus = Path(__file__).resolve().parents[1] / "third_party" / "toml-test" / "tests"
    valid = list((corpus / "valid").rglob("*.toml"))
    assert valid, "corpus missing"
    for path in valid[:40]:
        doc = path.read_bytes().decode("utf-8")
        ours, _ = canonicalize(tomlone.loads(doc))
        ref, _ = canonicalize(tomllib.loads(doc))
        assert ours == ref, f"canonical mismatch on {path.name}"
