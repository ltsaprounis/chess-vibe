"""The build's last step: compile the engine into a fresh numba cache.

The registry's build command ends with ``.venv/bin/python -m lab_engine.warm``.
It deletes every numba cache the package holds, so nothing compiled by an
earlier build survives, then imports the module the engine runs, which
compiles every function from its signature into a new cache. An engine
started afterwards loads that code instead of compiling it, which keeps its
start inside the handshake limits.

This isn't a build cache in the sense the repository rules out: the cache
lives inside the engine directory, every build starts it from nothing, and a
worktree's goes when the worktree does.
"""

from __future__ import annotations

import importlib
import logging
import shutil
import time
from pathlib import Path

from lab_engine.runtime import NUMBA_CACHE_ROOT, numba_cache_dir

logger = logging.getLogger(__name__)

ENGINE_MODULE = "lab_engine.uci"
"""The module ``python -m lab_engine`` runs. Importing it compiles all the engine uses."""


def warm(cache_root: Path = NUMBA_CACHE_ROOT) -> None:
    """Delete every cache under ``cache_root``, then compile the engine.

    Args:
        cache_root: Where the package's numba caches live.
    """
    if cache_root.exists():
        shutil.rmtree(cache_root)
    importlib.import_module(ENGINE_MODULE)


def main() -> None:
    """Warm the engine's own cache, and say where it went."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    started = time.perf_counter()
    warm()
    logger.info(
        "Compiled the engine into %s in %.1f s", numba_cache_dir(), time.perf_counter() - started
    )


if __name__ == "__main__":
    main()
