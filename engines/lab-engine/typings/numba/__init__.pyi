"""Type stubs for the parts of numba the lab engine uses.

numba ships no type information, and pyright's strict mode needs some. These
stubs declare only what the engine and its tests call. Add to them, rather
than loosening the type checker, when the engine starts using more of numba.
"""

from collections.abc import Callable
from typing import Any

from numba.core import config as config

def njit[F: Callable[..., Any]](
    signature: str, /, *, cache: bool = ..., nogil: bool = ...
) -> Callable[[F], F]: ...
def get_num_threads() -> int: ...
