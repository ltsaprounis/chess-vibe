"""Engine subprocess pool — manages lifecycle and concurrency limits.

Prevents resource exhaustion by capping the number of simultaneously
running engine subprocesses. Each engine is an opaque UCI binary;
this module never imports engine internals.
"""

from __future__ import annotations

import asyncio
import logging

from shared.uci_client import UCIClient

logger = logging.getLogger(__name__)

_DEFAULT_MAX_ENGINES = 4


class EnginePool:
    """Manages a pool of UCI engine subprocesses.

    Enforces a maximum concurrency limit and provides acquire/release
    semantics so callers can safely borrow engine instances.

    Attributes:
        max_engines: Maximum number of concurrent engine subprocesses.
    """

    def __init__(self, *, max_engines: int = _DEFAULT_MAX_ENGINES) -> None:
        """Initialise the pool.

        Args:
            max_engines: Maximum number of concurrent engines.
        """
        self.max_engines = max_engines
        self._semaphore = asyncio.Semaphore(max_engines)
        self._active_engines: list[UCIClient] = []
        self._lock = asyncio.Lock()

    @property
    def active_count(self) -> int:
        """Number of currently active engines."""
        return len(self._active_engines)

    async def acquire(self, engine_path: str, *, timeout: float = 10.0) -> UCIClient:
        """Start an engine subprocess and register it in the pool.

        Blocks until a slot is available (respects ``max_engines``).

        Args:
            engine_path: Path to the engine executable.
            timeout: UCI initialisation timeout in seconds.

        Returns:
            A started and ready :class:`UCIClient`.

        Raises:
            asyncio.TimeoutError: If the semaphore wait exceeds a
                reasonable time.
            shared.uci_client.UCIEngineError: If the engine fails to start.
        """
        await self._semaphore.acquire()
        client = UCIClient(engine_path, default_timeout=timeout)
        try:
            await client.start()
            await client.uci(timeout=timeout)
            await client.isready(timeout=timeout)
        except Exception:
            try:
                await client.quit()
            except Exception:
                logger.warning("Error quitting engine during acquire cleanup", exc_info=True)
            self._semaphore.release()
            raise

        async with self._lock:
            self._active_engines.append(client)

        logger.info("Acquired engine %s (active=%d)", engine_path, self.active_count)
        return client

    async def release(self, client: UCIClient) -> None:
        """Quit an engine subprocess and free the pool slot.

        Args:
            client: The engine client to release.
        """
        async with self._lock:
            if client in self._active_engines:
                self._active_engines.remove(client)

        try:
            await client.quit()
        except Exception:
            logger.warning("Error quitting engine during release", exc_info=True)
        finally:
            self._semaphore.release()
            logger.info("Released engine (active=%d)", self.active_count)

    async def shutdown(self) -> None:
        """Terminate all active engines (used during app shutdown)."""
        async with self._lock:
            engines = list(self._active_engines)
            self._active_engines.clear()

        for client in engines:
            try:
                await client.quit()
            except Exception:
                logger.warning("Error quitting engine during shutdown", exc_info=True)
            finally:
                self._semaphore.release()

        logger.info("Engine pool shut down")
