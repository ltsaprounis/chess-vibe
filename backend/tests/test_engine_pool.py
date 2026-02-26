"""Tests for the engine pool service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from backend.services.engine_pool import EnginePool


class TestEnginePool:
    """Tests for EnginePool lifecycle management."""

    def test_initial_state(self) -> None:
        pool = EnginePool(max_engines=2)
        assert pool.max_engines == 2
        assert pool.active_count == 0

    @pytest.mark.asyncio
    async def test_acquire_and_release(self) -> None:
        pool = EnginePool(max_engines=2)

        mock_client = MagicMock()
        mock_client.start = AsyncMock()
        mock_client.uci = AsyncMock()
        mock_client.isready = AsyncMock()
        mock_client.quit = AsyncMock()

        with patch("backend.services.engine_pool.UCIClient", return_value=mock_client):
            client = await pool.acquire("fake-engine")
            assert pool.active_count == 1
            assert client is mock_client

            await pool.release(client)
            assert pool.active_count == 0

    @pytest.mark.asyncio
    async def test_acquire_failure_releases_semaphore(self) -> None:
        pool = EnginePool(max_engines=1)

        mock_client = MagicMock()
        mock_client.start = AsyncMock(side_effect=RuntimeError("boom"))

        with patch("backend.services.engine_pool.UCIClient", return_value=mock_client):
            with pytest.raises(RuntimeError, match="boom"):
                await pool.acquire("bad-engine")

            assert pool.active_count == 0
            # Semaphore should be released — can acquire again
            assert pool._semaphore._value == 1  # type: ignore[reportAttributeAccessIssue]

    @pytest.mark.asyncio
    async def test_shutdown(self) -> None:
        pool = EnginePool(max_engines=2)

        mock_client = MagicMock()
        mock_client.start = AsyncMock()
        mock_client.uci = AsyncMock()
        mock_client.isready = AsyncMock()
        mock_client.quit = AsyncMock()

        with patch("backend.services.engine_pool.UCIClient", return_value=mock_client):
            await pool.acquire("engine1")
            assert pool.active_count == 1

            await pool.shutdown()
            assert pool.active_count == 0
            mock_client.quit.assert_called()
