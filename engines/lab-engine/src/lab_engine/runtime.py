"""Process settings that must be in place before numpy or numba is imported.

numpy's BLAS library and numba read their settings from environment
variables once, when they load. The registry's ``run`` command can't set
environment variables, because the SPRT runner splits it on whitespace and
starts it without a shell. So ``lab_engine/__init__.py`` applies these
settings in-process, before any module of the package imports numpy or numba.

Two settings:

- **One thread per pool.** An SPRT run plays several games at once, one
  engine per core. An engine that started more threads would compete with
  the others for cores and distort every game's clock.
- **A cache keyed by the source.** numba caches compiled code on disk and
  decides whether a cached function is current from the contents of its own
  file only. A function compiled with a call into another file keeps the old
  code of that call after the other file changes, without any error, and
  global arrays are frozen into the code in the same way. So the cache lives
  in a directory named after a hash of the package's Python files and of
  everything in its ``data`` folder: any edit to any of them means a new
  directory. ``check_source_unchanged`` covers an edit made while the engine
  is compiling.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from collections.abc import MutableMapping
from pathlib import Path

THREAD_LIMIT_VARIABLES: tuple[str, ...] = (
    "NUMBA_NUM_THREADS",  # numba's parallel thread pool
    "OMP_NUM_THREADS",  # OpenMP: numba's omp layer, and torch's intra-op pool
    "OPENBLAS_NUM_THREADS",  # numpy's BLAS in the Linux and older macOS wheels
    "MKL_NUM_THREADS",  # MKL builds of numpy, and torch on x86
    "VECLIB_MAXIMUM_THREADS",  # numpy's BLAS from Apple's Accelerate
)
"""Environment variables that size the thread pools numpy, numba and torch start."""

PACKAGE_DIR: Path = Path(__file__).resolve().parent
"""The ``lab_engine`` package directory, inside the engine directory."""

NUMBA_CACHE_ROOT: Path = PACKAGE_DIR / "__pycache__" / "numba"
"""Where numba's caches live: one directory per version of the source."""

DATA_DIR = "data"
"""The package folder for data that compiled code reads, such as weights."""

_DIGEST = re.compile(r"[0-9a-f]{16}")


def limit_threads(environ: MutableMapping[str, str]) -> None:
    """Limit every thread pool numpy, numba and torch can start to one thread.

    Values already set are overridden, so an inherited ``OMP_NUM_THREADS=8``
    can't raise the limit.

    Args:
        environ: The environment to change, normally ``os.environ``.
    """
    for name in THREAD_LIMIT_VARIABLES:
        environ[name] = "1"


def source_digest(package_dir: Path) -> str:
    """Return a short hash of the package's code and data.

    It covers every Python file in the package and every file in its
    ``data`` folder, since compiled code freezes the globals data is read
    into: each file's path relative to ``package_dir``, and its content. So
    it changes when one of them is edited, added, removed or renamed, and
    not when a file is only touched or the package moves. Hidden files and
    folders (editors' swap files, ``.DS_Store``) and ``__pycache__``, which
    holds the compiled code itself, don't count, and nor does a file that
    can't be read because it's gone, such as a link to nothing. Anything
    else in ``data`` counts, editors' backups included.

    Args:
        package_dir: The package directory to hash.

    Returns:
        The first 16 hex digits of a SHA-256 over the files.

    Raises:
        ValueError: If the package holds a visible link to a folder, whose
            contents the hash couldn't cover.
    """
    digest = hashlib.sha256()
    for path in _code_and_data(package_dir):
        try:
            content = hashlib.sha256(path.read_bytes()).hexdigest()
        except FileNotFoundError:
            continue  # gone since it was listed, or a link to nothing
        digest.update(f"{path.relative_to(package_dir).as_posix()} {content}\n".encode())
    return digest.hexdigest()[:16]


def _code_and_data(package_dir: Path) -> list[Path]:
    """List the files ``source_digest`` covers, in a fixed order."""
    found: list[Path] = []
    for directory, subdirectories, files in package_dir.walk():
        # Pruned in place, so the walk never enters them.
        subdirectories[:] = sorted(
            name for name in subdirectories if name != "__pycache__" and not name.startswith(".")
        )
        in_data = directory.relative_to(package_dir).parts[:1] == (DATA_DIR,)
        for name in sorted(files):
            if name.startswith("."):
                continue
            path = directory / name
            if path.is_dir():
                # The walk lists a link to a folder as a file, so what's behind
                # it would go unhashed: refuse it rather than guess.
                raise ValueError(
                    f"{path.relative_to(package_dir).as_posix()} is a link to a folder; "
                    "keep the engine's code and data in the package itself"
                )
            if in_data or name.endswith(".py"):
                found.append(path)
    return found


def numba_cache_dir(package_dir: Path = PACKAGE_DIR, cache_root: Path = NUMBA_CACHE_ROOT) -> Path:
    """Return the directory numba caches this version of the source in.

    Args:
        package_dir: The package whose source names the directory.
        cache_root: The directory that holds one cache per version.

    Returns:
        ``cache_root / source_digest(package_dir)``.
    """
    return cache_root / source_digest(package_dir)


def check_source_unchanged(
    expected: str, package_dir: Path = PACKAGE_DIR, cache_root: Path = NUMBA_CACHE_ROOT
) -> None:
    """Stop if the package changed after ``expected`` was taken from it.

    The package's ``__init__`` names numba's cache after the source as it
    finds it, and the compiled modules are read and compiled afterwards. A
    file edited in between would be compiled into the cache named after the
    source before the edit, which an engine started once the edit was undone
    would load. So, once every compiled module is imported, the source is
    hashed again. If it changed, that cache is deleted, and the engine stops
    rather than run code it can't vouch for.

    An edit undone before the second hash goes unseen. That needs the edit
    and its undo within the second or so it takes to start, or the few
    seconds it takes to compile from a cold cache.

    Args:
        expected: The digest the cache was named after.
        package_dir: The package to hash again.
        cache_root: Where the package's numba caches live.

    Raises:
        ValueError: If ``expected`` isn't a digest, so it can't name one
            cache under ``cache_root``.
        RuntimeError: If the package's digest is no longer ``expected``, or a
            link to a folder has appeared in it.
    """
    if not _DIGEST.fullmatch(expected):
        raise ValueError(f"not a source digest: {expected!r}")
    try:
        unchanged = source_digest(package_dir) == expected
    except ValueError:
        unchanged = False  # a link to a folder appeared: the package changed too
    if unchanged:
        return
    # Best effort: the engine stops either way.
    shutil.rmtree(cache_root / expected, ignore_errors=True)
    raise RuntimeError(
        "the engine's source changed while it was being compiled; the cache "
        "compiled from it is deleted, so start the engine again"
    )
