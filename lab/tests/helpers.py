"""Builders shared by the lab's tests."""

from __future__ import annotations

import copy
import json
from typing import Any

# A complete, valid configuration, as the TOML file's tables.
VALID_CONFIG: dict[str, Any] = {
    "concurrency": 2,
    "book": "books/test.epd",
    "correctness": {
        "commands": [["python3", "-c", "pass"]],
        "timeout_minutes": 5,
    },
    "fast": {
        "tc": "wtime=1000,btime=1000,winc=10,binc=10",
        "elo0": 0.0,
        "elo1": 10.0,
        "alpha": 0.05,
        "beta": 0.05,
        "max_games": 100,
        "max_time_losses": 1,
        "timeout_minutes": 30,
    },
    "confirm": {
        "tc": "wtime=4000,btime=4000,winc=40,binc=40",
        "elo0": 0.0,
        "elo1": 10.0,
        "alpha": 0.05,
        "beta": 0.05,
        "max_games": 200,
        "max_time_losses": 1,
        "timeout_minutes": 60,
    },
    "anchor": {
        "tc": "wtime=1000,btime=1000,winc=10,binc=10",
        "games": 20,
        "max_time_losses": 0,
        "timeout_minutes": 15,
    },
}


def config_data(**changes: Any) -> dict[str, Any]:
    """Return a deep copy of :data:`VALID_CONFIG` with dotted keys changed.

    A value of ``...`` deletes the key, so ``config_data(**{"fast.tc": ...})``
    is a config without ``fast.tc``.
    """
    data = copy.deepcopy(VALID_CONFIG)
    for dotted, value in changes.items():
        *tables, key = dotted.split(".")
        table = data
        for name in tables:
            table = table[name]
        if value is ...:
            del table[key]
        else:
            table[key] = value
    return data


def _toml_value(value: Any) -> str:
    """Write one TOML value: a string, number, boolean or list of them."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str | int | float):
        return json.dumps(value)
    if isinstance(value, list):
        items: list[Any] = value  # type: ignore[reportUnknownVariableType]
        return "[" + ", ".join(_toml_value(item) for item in items) + "]"
    raise TypeError(f"no TOML form for {value!r}")


def toml_dumps(data: dict[str, Any]) -> str:
    """Write a TOML document: top-level keys, then one table per dict value."""
    lines: list[str] = []
    tables: list[tuple[str, dict[str, Any]]] = []
    for key, value in data.items():
        if isinstance(value, dict):
            tables.append((key, value))  # type: ignore[reportUnknownArgumentType]
        else:
            lines.append(f"{key} = {_toml_value(value)}")
    for name, table in tables:
        lines.append(f"\n[{name}]")
        lines.extend(f"{key} = {_toml_value(value)}" for key, value in table.items())
    return "\n".join(lines) + "\n"
