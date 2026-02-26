"""Tests for the engine registry loader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from shared.engine_registry import EngineEntry, EngineRegistryError, load_registry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_registry(tmp_path: Path, data: object) -> Path:
    """Write *data* as JSON to a temp file and return its path."""
    registry = tmp_path / "engines.json"
    registry.write_text(json.dumps(data), encoding="utf-8")
    return registry


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


class TestLoadRegistryValid:
    """Tests for load_registry with valid inputs."""

    def test_single_entry_with_build(self, tmp_path: Path) -> None:
        data = [
            {
                "id": "random-engine",
                "name": "Random Engine",
                "dir": "engines/random-engine",
                "build": "uv venv .venv && uv pip install --python .venv/bin/python -e .",
                "run": ".venv/bin/python -m random_engine",
            }
        ]
        entries = load_registry(_write_registry(tmp_path, data))
        assert len(entries) == 1
        entry = entries[0]
        assert entry.id == "random-engine"
        assert entry.name == "Random Engine"
        assert entry.dir == "engines/random-engine"
        assert entry.build == "uv venv .venv && uv pip install --python .venv/bin/python -e ."
        assert entry.run == ".venv/bin/python -m random_engine"

    def test_null_build_field(self, tmp_path: Path) -> None:
        data = [
            {
                "id": "prebuilt-engine",
                "name": "Pre-built Engine",
                "dir": "engines/prebuilt",
                "build": None,
                "run": "./engine",
            }
        ]
        entries = load_registry(_write_registry(tmp_path, data))
        assert entries[0].build is None

    def test_multiple_entries(self, tmp_path: Path) -> None:
        data = [
            {
                "id": "engine-a",
                "name": "Engine A",
                "dir": "engines/a",
                "build": "cargo build --release",
                "run": "./target/release/engine_a",
            },
            {
                "id": "engine-b",
                "name": "Engine B",
                "dir": "engines/b",
                "build": None,
                "run": "./engine_b",
            },
        ]
        entries = load_registry(_write_registry(tmp_path, data))
        assert len(entries) == 2
        assert entries[0].id == "engine-a"
        assert entries[1].id == "engine-b"

    def test_empty_registry(self, tmp_path: Path) -> None:
        entries = load_registry(_write_registry(tmp_path, []))
        assert entries == []

    def test_returns_frozen_dataclass(self, tmp_path: Path) -> None:
        data = [
            {
                "id": "eng",
                "name": "Eng",
                "dir": "engines/eng",
                "build": None,
                "run": "./eng",
            }
        ]
        entries = load_registry(_write_registry(tmp_path, data))
        assert isinstance(entries[0], EngineEntry)
        with pytest.raises(AttributeError):
            entries[0].id = "other"  # type: ignore[misc]

    def test_real_engines_json(self) -> None:
        """The engines.json at repo root must parse successfully."""
        repo_root = Path(__file__).resolve().parents[2]
        registry_path = repo_root / "engines.json"
        entries = load_registry(registry_path)
        assert len(entries) >= 1
        ids = [e.id for e in entries]
        assert "random-engine" in ids

    def test_random_engine_entry_fields(self) -> None:
        """random-engine entry has the correct build and run commands."""
        repo_root = Path(__file__).resolve().parents[2]
        entries = load_registry(repo_root / "engines.json")
        entry = next(e for e in entries if e.id == "random-engine")
        assert entry.dir == "engines/random-engine"
        assert entry.build is not None
        assert "uv" in entry.build
        assert "random_engine" in entry.run


# ---------------------------------------------------------------------------
# Error-path tests
# ---------------------------------------------------------------------------


class TestLoadRegistryErrors:
    """Tests for load_registry with invalid / malformed inputs."""

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(EngineRegistryError, match="Cannot read registry file"):
            load_registry(tmp_path / "nonexistent.json")

    def test_invalid_json_raises(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "engines.json"
        bad_file.write_text("not json {{", encoding="utf-8")
        with pytest.raises(EngineRegistryError, match="not valid JSON"):
            load_registry(bad_file)

    def test_top_level_object_raises(self, tmp_path: Path) -> None:
        with pytest.raises(EngineRegistryError, match="must contain a JSON array"):
            load_registry(_write_registry(tmp_path, {"id": "x"}))

    def test_entry_not_object_raises(self, tmp_path: Path) -> None:
        with pytest.raises(EngineRegistryError, match="must be a JSON object"):
            load_registry(_write_registry(tmp_path, ["not-an-object"]))

    @pytest.mark.parametrize("field", ["id", "name", "dir", "run"])
    def test_missing_required_string_field_raises(self, tmp_path: Path, field: str) -> None:
        entry: dict[str, object] = {
            "id": "eng",
            "name": "Eng",
            "dir": "engines/eng",
            "build": None,
            "run": "./eng",
        }
        del entry[field]
        with pytest.raises(EngineRegistryError, match=f"missing required field '{field}'"):
            load_registry(_write_registry(tmp_path, [entry]))

    def test_missing_build_field_raises(self, tmp_path: Path) -> None:
        entry: dict[str, object] = {
            "id": "eng",
            "name": "Eng",
            "dir": "engines/eng",
            "run": "./eng",
        }
        with pytest.raises(EngineRegistryError, match="missing required field 'build'"):
            load_registry(_write_registry(tmp_path, [entry]))

    @pytest.mark.parametrize("field", ["id", "name", "dir", "run"])
    def test_non_string_required_field_raises(self, tmp_path: Path, field: str) -> None:
        entry: dict[str, object] = {
            "id": "eng",
            "name": "Eng",
            "dir": "engines/eng",
            "build": None,
            "run": "./eng",
        }
        entry[field] = 123
        with pytest.raises(EngineRegistryError, match=f"field '{field}' must be a string"):
            load_registry(_write_registry(tmp_path, [entry]))

    def test_build_non_string_non_null_raises(self, tmp_path: Path) -> None:
        entry: dict[str, object] = {
            "id": "eng",
            "name": "Eng",
            "dir": "engines/eng",
            "build": 42,
            "run": "./eng",
        }
        with pytest.raises(EngineRegistryError, match="field 'build' must be a string or null"):
            load_registry(_write_registry(tmp_path, [entry]))

    @pytest.mark.parametrize("field", ["id", "name", "dir", "run"])
    def test_empty_required_string_field_raises(self, tmp_path: Path, field: str) -> None:
        entry: dict[str, object] = {
            "id": "eng",
            "name": "Eng",
            "dir": "engines/eng",
            "build": None,
            "run": "./eng",
        }
        entry[field] = "   "
        with pytest.raises(EngineRegistryError, match=f"field '{field}' must not be empty"):
            load_registry(_write_registry(tmp_path, [entry]))

    def test_empty_build_string_raises(self, tmp_path: Path) -> None:
        entry: dict[str, object] = {
            "id": "eng",
            "name": "Eng",
            "dir": "engines/eng",
            "build": "  ",
            "run": "./eng",
        }
        with pytest.raises(EngineRegistryError, match="field 'build' must not be an empty string"):
            load_registry(_write_registry(tmp_path, [entry]))

    def test_duplicate_id_raises(self, tmp_path: Path) -> None:
        entry: dict[str, object] = {
            "id": "eng",
            "name": "Eng",
            "dir": "engines/eng",
            "build": None,
            "run": "./eng",
        }
        with pytest.raises(EngineRegistryError, match="duplicate engine id 'eng'"):
            load_registry(_write_registry(tmp_path, [entry, entry]))
