"""LabEngine: the baseline engine that the autoresearch loop improves (#190).

A deliberately plain UCI engine, written in Python and compiled with numba:
a 10x12 mailbox board, legal move generation, iterative-deepening alpha-beta
search, and a material plus piece-square evaluation. ``README.md`` beside
``pyproject.toml`` says what it leaves out and why.

Importing the package limits numpy's, numba's and torch's thread pools to
one thread each, and points numba's cache at a directory named after the
source. Both must happen before anything imports numpy or numba, which is why
they happen here. ``runtime.py`` explains them, and ``lab_engine.uci``, which
imports every compiled module, checks the source didn't change meanwhile.
"""

import os

from lab_engine.runtime import NUMBA_CACHE_ROOT, PACKAGE_DIR, limit_threads, source_digest

limit_threads(os.environ)

SOURCE_DIGEST = source_digest(PACKAGE_DIR)
"""The digest of the package as it was found, which names numba's cache."""

os.environ["NUMBA_CACHE_DIR"] = str(NUMBA_CACHE_ROOT / SOURCE_DIGEST)
