"""How the engine compiles code with numba.

Every compiled function in the engine is decorated with ``compiled``, which
gives numba the function's signature. With a signature, numba compiles the
function when its module is imported, rather than at its first call, and then
refuses to compile anything else for it. So once the engine has imported its
modules, which it does before it answers ``uci``, no search can stop to
compile.

The compiled code is cached on disk (``runtime.py`` says where) and runs
without holding the GIL, so the thread that reads UCI commands can take
``stop`` while a search runs.

Every array the compiled code works on holds int64, which keeps numba's
typing simple: the signatures use the two array types below, int64
scalars, and boolean and void results.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import numba

# numba reads NUMBA_CACHE_DIR when it's imported, and the package sets it
# before importing numba. A process that imported numba before this package,
# as a test can, would otherwise cache this source's code in numba's default
# place, keyed by file timestamps alone.
numba.config.CACHE_DIR = os.environ["NUMBA_CACHE_DIR"]

VECTOR = "int64[::1]"
"""Signature type of a one-dimensional, C-contiguous int64 array."""

TABLE = "int64[:, ::1]"
"""Signature type of a two-dimensional, C-contiguous int64 array."""


def compiled[F: Callable[..., Any]](signature: str) -> Callable[[F], F]:
    """Compile the decorated function with numba as its module is imported.

    Args:
        signature: The numba signature, such as ``f"int64({VECTOR}, int64)"``.

    Returns:
        A decorator that replaces the function with its compiled version,
        cached on disk and run without the GIL.
    """
    return numba.njit(signature, cache=True, nogil=True)
