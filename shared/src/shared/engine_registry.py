"""Engine registry loader for chess-vibe.

Loads and validates the ``engines.json`` file at the repo root, which declares
available chess engines in a language-agnostic way.

Each entry specifies how to build and launch an engine so that backend and
SPRT-runner components can discover and spawn engines without hard-coding
engine-specific details.

Typical usage::

    from shared.engine_registry import load_registry, EngineEntry

    entries = load_registry(Path("engines.json"))
    for entry in entries:
        print(entry.id, entry.run)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast


@dataclass(frozen=True)
class EngineEntry:
    """A single engine entry from the registry.

    Attributes:
        id: Unique identifier for the engine (e.g. ``"random-engine"``).
        name: Human-readable display name shown in the UI.
        dir: Path to the engine directory relative to the repo root.
        build: Shell command to build the engine, run from ``dir``. ``None``
            for pre-built binaries.
        run: Shell command to launch the UCI engine binary, run from ``dir``.
    """

    id: str
    name: str
    dir: str
    build: str | None
    run: str


class EngineRegistryError(Exception):
    """Raised when the engine registry file is missing or malformed."""


def _validate_entry(raw: Any, index: int) -> EngineEntry:
    """Validate a single raw registry entry and return an :class:`EngineEntry`.

    Args:
        raw: The raw parsed JSON value for this entry.
        index: Zero-based index of the entry in the array (for error messages).

    Returns:
        A validated :class:`EngineEntry`.

    Raises:
        EngineRegistryError: If the entry is not a dict or is missing / has
            invalid required fields.
    """
    if not isinstance(raw, dict):
        raise EngineRegistryError(
            f"Entry at index {index} must be a JSON object, got {type(raw).__name__}"
        )

    entry_dict = cast(dict[str, Any], raw)

    required_str_fields = ("id", "name", "dir", "run")
    for field in required_str_fields:
        if field not in entry_dict:
            raise EngineRegistryError(f"Entry at index {index} is missing required field '{field}'")
        value: Any = entry_dict[field]
        if not isinstance(value, str):
            raise EngineRegistryError(
                f"Entry at index {index}: field '{field}' must be a string,"
                f" got {type(value).__name__}"
            )
        if not value.strip():
            raise EngineRegistryError(f"Entry at index {index}: field '{field}' must not be empty")

    if "build" not in entry_dict:
        raise EngineRegistryError(f"Entry at index {index} is missing required field 'build'")
    build_raw: Any = entry_dict["build"]
    if build_raw is not None and not isinstance(build_raw, str):
        raise EngineRegistryError(
            f"Entry at index {index}: field 'build' must be a string or null,"
            f" got {type(build_raw).__name__}"
        )
    if isinstance(build_raw, str) and not build_raw.strip():
        raise EngineRegistryError(
            f"Entry at index {index}: field 'build' must not be an empty string"
            " (use null for pre-built binaries)"
        )
    build_value: str | None = build_raw if isinstance(build_raw, str) else None

    return EngineEntry(
        id=cast(str, entry_dict["id"]),
        name=cast(str, entry_dict["name"]),
        dir=cast(str, entry_dict["dir"]),
        build=build_value,
        run=cast(str, entry_dict["run"]),
    )


def load_registry(path: Path) -> list[EngineEntry]:
    """Load and validate the engine registry JSON file.

    Args:
        path: Path to the ``engines.json`` file.

    Returns:
        A list of validated :class:`EngineEntry` objects.

    Raises:
        EngineRegistryError: If the file cannot be read, is not valid JSON,
            or contains malformed entries.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EngineRegistryError(f"Cannot read registry file '{path}': {exc}") from exc

    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EngineRegistryError(f"Registry file '{path}' is not valid JSON: {exc}") from exc

    if not isinstance(data, list):
        raise EngineRegistryError(
            f"Registry file '{path}' must contain a JSON array, got {type(data).__name__}"
        )

    entries: list[EngineEntry] = []
    seen_ids: set[str] = set()
    raw_list = cast(list[Any], data)
    for i, raw in enumerate(raw_list):
        entry = _validate_entry(raw, i)
        if entry.id in seen_ids:
            raise EngineRegistryError(f"Entry at index {i}: duplicate engine id '{entry.id}'")
        seen_ids.add(entry.id)
        entries.append(entry)

    return entries
