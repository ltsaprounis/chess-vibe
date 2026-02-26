"""Shared library for chess-vibe: UCI client, time control models, and engine registry."""

from shared.engine_registry import EngineEntry, EngineRegistryError, load_registry
from shared.time_control import (
    DepthTimeControl,
    FixedTimeControl,
    IncrementTimeControl,
    NodesTimeControl,
    TimeControl,
    TimeControlType,
)
from shared.uci_client import (
    BestMove,
    UCIClient,
    UCIEngineError,
    UCIError,
    UCIInfo,
    UCIScore,
    UCITimeoutError,
    parse_bestmove,
    parse_info_line,
)

__all__ = [
    "BestMove",
    "DepthTimeControl",
    "EngineEntry",
    "EngineRegistryError",
    "FixedTimeControl",
    "IncrementTimeControl",
    "NodesTimeControl",
    "TimeControl",
    "TimeControlType",
    "UCIClient",
    "UCIEngineError",
    "UCIError",
    "UCIInfo",
    "UCIScore",
    "UCITimeoutError",
    "load_registry",
    "parse_bestmove",
    "parse_info_line",
]
