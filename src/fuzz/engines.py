"""Adapter layer: every parser behind one uniform interface.

Each engine returns ``EngineResult``:

* ``ok``        — whether the document was accepted
* ``canonical`` — a structure of hashable primitives (see :mod:`canonical`)
* ``error_type``— parser exception class name when rejected
* ``crash``     — an exception that is *not* the parser's ordinary reject
                  (e.g. IndexError/AssertionError): a definite engine bug

Cross-engine crash isolation matters: one engine raising an unexpected
exception must never abort the whole fuzzing run.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import math
import tomllib

from tomlone import TOMLDecodeError, loads as tomlone_loads

try:
    import rtoml
except ImportError:  # pragma: no cover
    rtoml = None

try:
    import pytomlpp
except ImportError:  # pragma: no cover
    pytomlpp = None

from .canonical import canonicalize


@dataclasses.dataclass
class EngineResult:
    name: str
    ok: bool
    canonical: object = None
    error_type: str | None = None
    crash: str | None = None
    precision_notes: tuple = ()


def _reject_error_types() -> dict[str, tuple[type[BaseException], ...]]:
    """Ordinary 'input rejected' exception types per engine."""
    types: dict[str, tuple[type[BaseException], ...]] = {
        "tomlone": (TOMLDecodeError,),
        "tomllib": (tomllib.TOMLDecodeError,),
    }
    if rtoml is not None:
        types["rtoml"] = (rtoml.TomlParsingError,)
    if pytomlpp is not None:
        types["pytomlpp"] = (pytomlpp.DecodeError,)
    return types


_REJECT_ERRORS = _reject_error_types()


def available_engines() -> dict[str, object]:
    engines = {"tomlone": _TomlOneAdapter(), "tomllib": tomllib}
    if rtoml is not None:
        engines["rtoml"] = rtoml
    if pytomlpp is not None:
        engines["pytomlpp"] = pytomlpp
    return engines


# Measured TOML dialect of each engine on this runtime. tomllib and rtoml
# have moved to (partial) 1.1; pytomlpp remains the strict 1.0 reference.
# The differential report uses this to interpret 1.0-vs-1.1 splits correctly.
ENGINE_TOML_VERSION = {
    "tomlone": "1.0",
    "pytomlpp": "1.0",
    "rtoml": "1.1-partial",
    "tomllib": "1.1",
}

# The engine whose accept/reject decision defines strict TOML 1.0 in-process,
# alongside the official toml-test v1.0.0 corpus.
STRICT_1_0_REFERENCE = "pytomlpp"


class _TomlOneAdapter:
    """Small shim so tomlone presents the same ``loads`` interface."""
    loads = staticmethod(tomlone_loads)


def run_engine(name: str, module: object, text: str) -> EngineResult:
    try:
        parsed = module.loads(text)
    except KeyboardInterrupt:
        raise
    except Exception as exc:  # noqa: BLE001 - isolation by design
        if isinstance(exc, _REJECT_ERRORS.get(name, ())):
            return EngineResult(name=name, ok=False, error_type=type(exc).__name__)
        return EngineResult(
            name=name, ok=False, error_type=type(exc).__name__,
            crash=f"{type(exc).__name__}: {exc}",
        )
    canon, notes = canonicalize(parsed)
    return EngineResult(name=name, ok=True, canonical=canon,
                        precision_notes=tuple(notes))
