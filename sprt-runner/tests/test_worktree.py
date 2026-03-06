"""Tests for git worktree management."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import sprt_runner.worktree as _worktree_mod
from sprt_runner.worktree import (
    EngineSpec,
    WorktreeError,
    cleanup_worktree,
    parse_engine_spec,
    resolve_engine_path,
)

# Access _create_worktree via the module to avoid pyright reportPrivateUsage
_create_worktree = _worktree_mod._create_worktree  # pyright: ignore[reportPrivateUsage]


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


class TestCreateWorktree:
    """Tests for _create_worktree stale directory handling."""

    @pytest.mark.asyncio
    async def test_valid_worktree_returned_as_is(self, tmp_path: Path) -> None:
        """A directory with a .git file is a valid worktree and returned without recreation."""
        worktree_path = tmp_path / ".worktrees" / "abc123"
        worktree_path.mkdir(parents=True, exist_ok=True)
        # Valid worktrees have a .git file (not directory)
        (worktree_path / ".git").write_text("gitdir: /some/repo/.git/worktrees/abc123\n")

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            result = await _create_worktree(tmp_path, "abc123")
            assert result == worktree_path
            # Should NOT call git worktree add — it's already valid
            mock_exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_stale_directory_recreated(self, tmp_path: Path) -> None:
        """A directory without a .git file is stale and must be removed and recreated."""
        worktree_path = tmp_path / ".worktrees" / "abc123"
        worktree_path.mkdir(parents=True, exist_ok=True)
        # Put a marker file to prove the directory gets removed
        (worktree_path / "stale_marker.txt").write_text("I am stale")

        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_process
            result = await _create_worktree(tmp_path, "abc123")
            assert result == worktree_path
            # Should have called git worktree add to recreate
            mock_exec.assert_called_once()
            call_args = mock_exec.call_args[0]
            assert call_args[:3] == ("git", "worktree", "add")

    @pytest.mark.asyncio
    async def test_stale_directory_removed_before_recreation(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The stale directory should be removed before attempting to recreate."""
        worktree_path = tmp_path / ".worktrees" / "abc123"
        worktree_path.mkdir(parents=True, exist_ok=True)
        (worktree_path / "leftover_file.txt").write_text("leftover")

        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"", b""))

        with (
            patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec,
            caplog.at_level(logging.WARNING),
        ):
            mock_exec.return_value = mock_process
            await _create_worktree(tmp_path, "abc123")

        assert any("Stale worktree" in msg for msg in caplog.messages)
        # The leftover file from the stale directory should have been removed
        assert not (worktree_path / "leftover_file.txt").exists()

    @pytest.mark.asyncio
    async def test_stale_directory_recreation_failure_raises(self, tmp_path: Path) -> None:
        """If recreation of a stale worktree fails, a clear error is raised."""
        worktree_path = tmp_path / ".worktrees" / "abc123"
        worktree_path.mkdir(parents=True, exist_ok=True)
        # No .git file → stale

        mock_process = AsyncMock()
        mock_process.returncode = 128
        mock_process.communicate = AsyncMock(return_value=(b"", b"fatal: not a commit"))

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_process
            with pytest.raises(WorktreeError, match="Failed to create worktree"):
                await _create_worktree(tmp_path, "abc123")


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
            run_cmd, _engine_dir = await resolve_engine_path(spec, repo_root=tmp_path)
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
            run_cmd, _resolved_dir = await resolve_engine_path(spec, repo_root=tmp_path)
            assert run_cmd == "./engine"
            # Build should have been invoked
            mock_exec.assert_called_once()

    @pytest.mark.asyncio
    async def test_worktree_fallback_to_repo_root(self, tmp_path: Path) -> None:
        """When engines.json is missing in worktree, falls back to repo root."""
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
        # engines.json only in repo root, NOT in worktree
        registry_path = tmp_path / "engines.json"
        registry_path.write_text(json.dumps(registry))

        spec = EngineSpec(engine_id="test-engine", commit="abc123")

        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"", b""))

        # Create worktree dir without engines.json
        worktree_path = tmp_path / ".worktrees" / "abc123"
        worktree_path.mkdir(parents=True, exist_ok=True)

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_process
            run_cmd, engine_dir = await resolve_engine_path(spec, repo_root=tmp_path)
            assert run_cmd == "run_cmd"
            # Engine dir should be resolved from worktree root
            assert engine_dir == worktree_path / "engines/test-engine"

    @pytest.mark.asyncio
    async def test_no_fallback_without_commit(self, tmp_path: Path) -> None:
        """Without a commit, missing engines.json should still raise."""
        spec = EngineSpec(engine_id="test", commit=None)
        with pytest.raises(WorktreeError, match="Cannot read registry"):
            await resolve_engine_path(spec, repo_root=tmp_path)


class TestCleanupWorktree:
    """Tests for worktree cleanup."""

    @pytest.mark.asyncio
    async def test_cleanup_runs_git_worktree_remove(self, tmp_path: Path) -> None:
        """cleanup_worktree should run git worktree remove --force."""
        worktree_path = tmp_path / ".worktrees" / "abc123"
        worktree_path.mkdir(parents=True, exist_ok=True)

        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_process
            await cleanup_worktree(worktree_path, repo_root=tmp_path)

            mock_exec.assert_called_once_with(
                "git",
                "worktree",
                "remove",
                "--force",
                str(worktree_path),
                cwd=str(tmp_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

    @pytest.mark.asyncio
    async def test_cleanup_logs_warning_on_failure(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Failed worktree removal should log a warning, not raise."""
        worktree_path = tmp_path / ".worktrees" / "abc123"
        worktree_path.mkdir(parents=True, exist_ok=True)

        mock_process = AsyncMock()
        mock_process.returncode = 1
        mock_process.communicate = AsyncMock(return_value=(b"", b"removal failed"))

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_process
            with caplog.at_level(logging.WARNING):
                await cleanup_worktree(worktree_path, repo_root=tmp_path)

        assert any("Failed to remove worktree" in msg for msg in caplog.messages)

    @pytest.mark.asyncio
    async def test_cleanup_logs_warning_on_exception(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Unexpected exceptions during cleanup should be caught and logged."""
        worktree_path = tmp_path / ".worktrees" / "abc123"
        worktree_path.mkdir(parents=True, exist_ok=True)

        with (
            patch(
                "asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                side_effect=OSError("git not found"),
            ),
            caplog.at_level(logging.WARNING),
        ):
            await cleanup_worktree(worktree_path, repo_root=tmp_path)

        assert any("Failed to remove worktree" in msg for msg in caplog.messages)

    @pytest.mark.asyncio
    async def test_cleanup_nonexistent_path_is_noop(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Cleaning up a path that doesn't exist should be a no-op."""
        worktree_path = tmp_path / ".worktrees" / "nonexistent"

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            with caplog.at_level(logging.DEBUG):
                await cleanup_worktree(worktree_path, repo_root=tmp_path)

            mock_exec.assert_not_called()
