"""Find every function the engine compiles with numba."""

from __future__ import annotations

import importlib
import pkgutil

import lab_engine
from numba.core.dispatcher import Dispatcher


def compiled_functions() -> dict[str, Dispatcher]:
    """Return every numba-compiled function in the package, by qualified name.

    Imports every module but ``__main__``, which would start the engine.
    """
    found: dict[str, Dispatcher] = {}
    for info in pkgutil.iter_modules(lab_engine.__path__):
        if info.name == "__main__":
            continue
        module = importlib.import_module(f"lab_engine.{info.name}")
        for name, value in vars(module).items():
            if isinstance(value, Dispatcher) and value.py_func.__module__ == module.__name__:
                found[f"{module.__name__}.{name}"] = value
    return found
