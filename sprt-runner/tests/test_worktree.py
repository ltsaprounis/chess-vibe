"""Tests for git worktree management."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sprt_runner.worktree import (
    EngineSpec,
    WorktreeError,
    parse_engine_spec,
    resolve_engine_path,
)


class TestParseEngineSpec:
    """Tests for parsing ENGINE[:COMMIT] specifications."""

    def test_engine_only(self) -> None:
        spec = parse_engine_spec("random-engine")
        assert spec.engine_id == "random-engine"
        assert spec.commit is None

    def test_engine_with_commit(self) -> None:
        spec = parse_engine_spec("random-engine:abc123")
        assert spec.engine_id == "random-engine"
        assert spec.commit == "abc123"

    def test_engine_with_long_sha(self) -> None:
        sha = "abc123def456789012345678901234567890abcd"
        spec = parse_engine_spec(f"my-engine:{sha}")
        assert spec.engine_id == "my-engine"
        assert spec.commit == sha

    def test_empty_engine_raises(self) -> None:
        with pytest.raises(ValueError, match="Engine ID must not be empty"):
            parse_engine_spec("")

    def test_empty_commit_uses_none(self) -> None:
        spec = parse_engine_spec("engine:")
        assert spec.engine_id == "engine"
        assert spec.commit is None


class TestEngineSpec:
    """Tests for EngineSpec dataclass."""

    def test_frozen(self) -> None:
        spec = EngineSpec(engine_id="test", commit=None)
        with pytest.raises(AttributeError):
            spec.engine_id = "other"  # type: ignore[misc]

    def test_equality(self) -> None:
        s1 = EngineSpec(engine_id="eng", commit="abc")
        s2 = EngineSpec(engine_id="eng", commit="abc")
        assert s1 == s2


class TestResolveEnginePath:
    """Tests for resolving engine binary paths."""

    @pytest.mark.asyncio
    async def test_resolve_without_commit(self, tmp_path: Path) -> None:
        """Without a commit, resolves from current repo root."""
        import json

        registry = [
            {
                "id": "test-engine",
                "name": "Test Engine",
                "dir": "engines/test-engine",
                "build": None,
                "run": ".venv/bin/python -m test_engine",
            }
        ]
        registry_path = tmp_path / "engines.json"
        registry_path.write_text(json.dumps(registry))

        spec = EngineSpec(engine_id="test-engine", commit=None)
        run_cmd, engine_dir = await resolve_engine_path(spec, repo_root=tmp_path)
        assert run_cmd == ".venv/bin/python -m test_engine"
        assert engine_dir == tmp_path / "engines/test-engine"

    @pytest.mark.asyncio
    async def test_engine_not_in_registry(self, tmp_path: Path) -> None:
        """Unknown engine ID should raise WorktreeError."""
        import json

        registry: list[dict[str, str | None]] = [
            {
                "id": "other-engine",
                "name": "Other",
                "dir": "engines/other",
                "build": None,
                "run": "run_cmd",
            }
        ]
        registry_path = tmp_path / "engines.json"
        registry_path.write_text(json.dumps(registry))

        spec = EngineSpec(engine_id="nonexistent", commit=None)
        with pytest.raises(WorktreeError, match="Engine 'nonexistent' not found"):
            await resolve_engine_path(spec, repo_root=tmp_path)

    @pytest.mark.asyncio
    async def test_missing_registry(self, tmp_path: Path) -> None:
        """Missing engines.json should raise WorktreeError."""
        spec = EngineSpec(engine_id="test", commit=None)
        with pytest.raises(WorktreeError, match="Cannot read registry"):
            await resolve_engine_path(spec, repo_root=tmp_path)

    @pytest.mark.asyncio
    async def test_resolve_with_commit_creates_worktree(self, tmp_path: Path) -> None:
        """With a commit, should attempt to create a worktree."""
        import json

        registry = [
            {
                "id": "test-engine",
                "name": "Test",
                "dir": "engines/test-engine",
                "build": None,
                "run": "run_cmd",
            }
        ]
        registry_path = tmp_path / "engines.json"
        registry_path.write_text(json.dumps(registry))

        spec = EngineSpec(engine_id="test-engine", commit="abc123")

        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"", b""))

        worktree_path = tmp_path / ".worktrees" / "abc123"
        worktree_registry = worktree_path / "engines.json"
        worktree_registry.parent.mkdir(parents=True, exist_ok=True)
        worktree_registry.write_text(json.dumps(registry))

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_process
            run_cmd, engine_dir = await resolve_engine_path(spec, repo_root=tmp_path)
            assert run_cmd == "run_cmd"

    @pytest.mark.asyncio
    async def test_resolve_with_build_runs_build(self, tmp_path: Path) -> None:
        """If engine has a build command, it should be executed."""
        import json

        registry = [
            {
                "id": "test-engine",
                "name": "Test",
                "dir": "engines/test-engine",
                "build": "make build",
                "run": "./engine",
            }
        ]
        registry_path = tmp_path / "engines.json"
        registry_path.write_text(json.dumps(registry))

        # Create the engine directory
        engine_dir = tmp_path / "engines/test-engine"
        engine_dir.mkdir(parents=True, exist_ok=True)

        spec = EngineSpec(engine_id="test-engine", commit=None)

        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"build output", b""))

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_process
            run_cmd, resolved_dir = await resolve_engine_path(spec, repo_root=tmp_path)
            assert run_cmd == "./engine"
            # Build should have been invoked
            mock_exec.assert_called_once()
