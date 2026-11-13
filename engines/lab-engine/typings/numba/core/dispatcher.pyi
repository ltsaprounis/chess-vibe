from collections import Counter
from types import FunctionType
from typing import Any, NamedTuple

class _CompileStats(NamedTuple):
    cache_path: str
    cache_hits: Counter[Any]
    cache_misses: Counter[Any]

class Dispatcher:
    py_func: FunctionType
    @property
    def signatures(self) -> list[Any]: ...
    @property
    def stats(self) -> _CompileStats: ...
    def compile(self, sig: Any) -> Any: ...
