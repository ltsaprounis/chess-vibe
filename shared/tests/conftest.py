"""Pytest configuration for shared tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _repo_root() -> Path:
    """Return the repository root directory."""
    return Path(__file__).resolve().parents[2]


def _random_engine_command() -> str | None:
    """Resolve the random-engine run command from engines.json.

    Returns the full command string if the engine venv exists, None otherwise.
    """
    engines_json = _repo_root() / "engines.json"
    if not engines_json.exists():
        return None

    engines = json.loads(engines_json.read_text())
    for engine in engines:
        if engine["id"] == "random-engine":
            engine_dir = _repo_root() / engine["dir"]
            venv_python = engine_dir / ".venv" / "bin" / "python"
            if venv_python.exists():
                return f"{venv_python} -m random_engine"
            return None
    return None


@pytest.fixture
def random_engine_command() -> str:
    """Provide the random-engine command string.

    Skips the test if the random-engine venv is not built.
    """
    cmd = _random_engine_command()
    if cmd is None:
        pytest.skip("random-engine venv not built — run 'make setup' first")
    return cmd
