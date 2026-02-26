"""Git worktree management for commit-based SPRT testing.

Creates git worktrees for ``ENGINE[:COMMIT]`` specs, reads ``engines.json``
from the worktree (or current tree), optionally builds the engine, and
returns the engine binary path and run command.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from shared.engine_registry import EngineRegistryError, load_registry

logger = logging.getLogger(__name__)


class WorktreeError(Exception):
    """Raised when worktree or engine resolution fails."""


@dataclass(frozen=True)
class EngineSpec:
    """Parsed engine specification.

    Attributes:
        engine_id: Engine identifier from engines.json.
        commit: Git commit SHA, or None for the current working tree.
    """

    engine_id: str
    commit: str | None


def parse_engine_spec(spec: str) -> EngineSpec:
    """Parse an ``ENGINE[:COMMIT]`` specification string.

    Args:
        spec: Engine specification, e.g. ``"random-engine"`` or
            ``"random-engine:abc123"``.

    Returns:
        Parsed EngineSpec.

    Raises:
        ValueError: If the engine ID is empty.
    """
    if ":" in spec:
        engine_id, commit = spec.split(":", 1)
        commit = commit if commit else None
    else:
        engine_id = spec
        commit = None

    if not engine_id:
        raise ValueError("Engine ID must not be empty")

    return EngineSpec(engine_id=engine_id, commit=commit)


async def _create_worktree(repo_root: Path, commit: str) -> Path:
    """Create a git worktree at the given commit.

    Args:
        repo_root: Path to the git repository root.
        commit: Git commit SHA.

    Returns:
        Path to the created worktree directory.

    Raises:
        WorktreeError: If the worktree cannot be created.
    """
    worktree_path = repo_root / ".worktrees" / commit
    if worktree_path.exists():
        logger.info("Worktree already exists: %s", worktree_path)
        return worktree_path

    logger.info("Creating worktree at %s for commit %s", worktree_path, commit)
    process = await asyncio.create_subprocess_exec(
        "git",
        "worktree",
        "add",
        str(worktree_path),
        commit,
        cwd=str(repo_root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()

    if process.returncode != 0:
        raise WorktreeError(
            f"Failed to create worktree for commit '{commit}': {stderr.decode().strip()}"
        )

    return worktree_path


async def _build_engine(engine_dir: Path, build_cmd: str) -> None:
    """Run the build command for an engine.

    Args:
        engine_dir: Directory where the engine is located.
        build_cmd: Shell command to build the engine.

    Raises:
        WorktreeError: If the build fails.
    """
    logger.info("Building engine in %s: %s", engine_dir, build_cmd)
    process = await asyncio.create_subprocess_exec(
        "sh",
        "-c",
        build_cmd,
        cwd=str(engine_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        raise WorktreeError(
            f"Engine build failed in '{engine_dir}':\n"
            f"stdout: {stdout.decode().strip()}\n"
            f"stderr: {stderr.decode().strip()}"
        )

    logger.info("Build succeeded in %s", engine_dir)


async def resolve_engine_path(
    spec: EngineSpec,
    *,
    repo_root: Path,
) -> tuple[str, Path]:
    """Resolve an engine specification to a run command and directory.

    For specs without a commit, reads ``engines.json`` from the current
    repo root. For specs with a commit, creates a git worktree at that
    commit and reads the registry from there. If ``engines.json`` is
    absent in the worktree, falls back to reading from the repo root.

    If the engine has a build command, it is executed before returning.

    Args:
        spec: Parsed engine specification.
        repo_root: Path to the git repository root.

    Returns:
        Tuple of (run_command, engine_directory).

    Raises:
        WorktreeError: If the engine cannot be found or built.
    """
    if spec.commit is not None:
        worktree_path = await _create_worktree(repo_root, spec.commit)
        effective_root = worktree_path
    else:
        effective_root = repo_root

    # Load engine registry — fall back to repo root when absent in worktree
    registry_path = effective_root / "engines.json"
    try:
        entries = load_registry(registry_path)
    except EngineRegistryError:
        if effective_root != repo_root:
            logger.info(
                "engines.json not found in worktree %s, falling back to %s",
                effective_root,
                repo_root,
            )
            fallback_path = repo_root / "engines.json"
            try:
                entries = load_registry(fallback_path)
            except EngineRegistryError as e2:
                raise WorktreeError(
                    f"Cannot read registry at '{fallback_path}' (fallback): {e2}"
                ) from e2
        else:
            raise WorktreeError(f"Cannot read registry at '{registry_path}'")

    # Find the engine entry
    entry = None
    for e in entries:
        if e.id == spec.engine_id:
            entry = e
            break

    if entry is None:
        available = [e.id for e in entries]
        raise WorktreeError(
            f"Engine '{spec.engine_id}' not found in registry. Available engines: {available}"
        )

    engine_dir = effective_root / entry.dir

    # Build if needed
    if entry.build is not None:
        await _build_engine(engine_dir, entry.build)

    return entry.run, engine_dir
