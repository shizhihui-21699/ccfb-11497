"""tomlone — a from-scratch, dependency-free TOML 1.0.0 parser.

Public API mirrors the standard library ``tomllib``:

    tomlone.loads(s)   -> dict
    tomlone.load(fp)    -> dict  (binary file, like tomllib.load)

On malformed input, :class:`TOMLDecodeError` is raised. It is a subclass of
:class:`ValueError`, the same convention ``tomllib`` uses.
"""

from ._parser import TOMLDecodeError, load, loads

__all__ = ["load", "loads", "TOMLDecodeError", "__version__"]
__version__ = "0.1.0"
