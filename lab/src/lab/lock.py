"""One lab command at a time in a repository.

The runner builds each ``ENGINE:COMMIT`` in ``.worktrees/<commit>`` under the
repository root, and the first run to finish removes it, so two runs at once
in one repository break each other. ``lab attempt`` and ``lab anchor`` each
hold an exclusive lock on ``lab.lock`` in the repository's git directory,
shared by all its worktrees, while they run. The lock belongs to the open
file, so it goes when the process does, however it ends.

The lock can't see the runner started some other way, such as from the web
UI's SPRT page, so don't start runs there while the lab runs.
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from lab.refusal import Refused

LOCK_NAME = "lab.lock"


@contextmanager
def exclusive(directory: Path) -> Iterator[None]:
    """Hold the lab's lock in ``directory``, or refuse if another command holds it.

    Args:
        directory: The repository's git directory.

    Raises:
        Refused: If another lab command holds the lock. Its message gives the
            holder's pid.
    """
    fd = os.open(directory / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            holder = os.pread(fd, 64, 0).decode(errors="replace").strip() or "unknown"
            raise Refused(
                "busy",
                f"another lab command is running in this repository (pid {holder}), and the "
                "runner allows one run at a time: wait for it to finish",
            ) from None
        os.ftruncate(fd, 0)
        os.pwrite(fd, f"{os.getpid()}\n".encode(), 0)
        yield
    finally:
        os.close(fd)
