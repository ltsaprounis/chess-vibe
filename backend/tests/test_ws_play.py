"""Tests for the play WebSocket module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from backend.ws.play import _resolve_engine_path  # type: ignore[reportPrivateUsage]
from shared.engine_registry import EngineEntry


class TestResolveEnginePath:
    """Tests for _resolve_engine_path."""

    def test_uses_repo_root_for_engine_dir(self) -> None:
        """Engine directory should be resolved relative to repo root."""
        fake_root = Path("/fake/repo/root")
        entry = EngineEntry(
            id="my-engine",
            name="My Engine",
            dir="engines/my-engine",
            build=None,
            run=".venv/bin/python -m my_engine",
        )
        registry_path = fake_root / "engines.json"

        with (
            patch("backend.ws.play.load_registry", return_value=[entry]),
            patch("backend.ws.play.get_repo_root", return_value=fake_root),
        ):
            result = _resolve_engine_path("my-engine", registry_path)

        expected = str(fake_root / "engines/my-engine" / ".venv/bin/python") + " -m my_engine"
        assert result == expected

    def test_engine_not_found_raises(self) -> None:
        """ValueError is raised when the engine ID is not in the registry."""
        with (
            patch("backend.ws.play.load_registry", return_value=[]),
            pytest.raises(ValueError, match="not found"),
        ):
            _resolve_engine_path("missing", Path("/some/engines.json"))

    def test_run_without_parts_returns_run(self) -> None:
        """When run is empty, return it as-is."""
        fake_root = Path("/fake/repo/root")
        entry = EngineEntry(
            id="empty-run",
            name="Empty",
            dir="engines/empty",
            build=None,
            run="",
        )
        with (
            patch("backend.ws.play.load_registry", return_value=[entry]),
            patch("backend.ws.play.get_repo_root", return_value=fake_root),
        ):
            result = _resolve_engine_path("empty-run", Path("/any/engines.json"))

        assert result == ""
