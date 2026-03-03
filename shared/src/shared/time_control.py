"""Time control models for UCI chess engines.

Provides typed dataclasses for the three main time control variants:
fixed-time (movetime), increment (wtime/btime/winc/binc), and
depth/nodes limits.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TimeControlType(Enum):
    """Enumeration of supported time control types."""

    FIXED_TIME = "fixed_time"
    INCREMENT = "increment"
    DEPTH = "depth"
    NODES = "nodes"


@dataclass(frozen=True)
class FixedTimeControl:
    """Fixed time per move (movetime) in milliseconds.

    Attributes:
        movetime_ms: Time per move in milliseconds. Must be positive.
    """

    movetime_ms: int

    def __post_init__(self) -> None:
        """Validate movetime_ms is positive."""
        if self.movetime_ms <= 0:
            raise ValueError(f"movetime_ms must be positive, got {self.movetime_ms}")

    @property
    def type(self) -> TimeControlType:
        """Return the time control type."""
        return TimeControlType.FIXED_TIME

    def to_uci_params(self) -> str:
        """Convert to UCI go command parameters.

        Returns:
            UCI parameter string for the go command.
        """
        return f"movetime {self.movetime_ms}"


@dataclass(frozen=True)
class IncrementTimeControl:
    """Clock-based time control with optional increment.

    Attributes:
        wtime_ms: White time remaining in milliseconds. Must be non-negative.
        btime_ms: Black time remaining in milliseconds. Must be non-negative.
        winc_ms: White increment per move in milliseconds. Must be non-negative.
        binc_ms: Black increment per move in milliseconds. Must be non-negative.
        moves_to_go: Number of moves until next time control. Must be positive if set.
    """

    wtime_ms: int
    btime_ms: int
    winc_ms: int = 0
    binc_ms: int = 0
    moves_to_go: int | None = None

    def __post_init__(self) -> None:
        """Validate time values are non-negative and moves_to_go is positive if set."""
        if self.wtime_ms < 0:
            raise ValueError(f"wtime_ms must be non-negative, got {self.wtime_ms}")
        if self.btime_ms < 0:
            raise ValueError(f"btime_ms must be non-negative, got {self.btime_ms}")
        if self.winc_ms < 0:
            raise ValueError(f"winc_ms must be non-negative, got {self.winc_ms}")
        if self.binc_ms < 0:
            raise ValueError(f"binc_ms must be non-negative, got {self.binc_ms}")
        if self.moves_to_go is not None and self.moves_to_go <= 0:
            raise ValueError(f"moves_to_go must be positive, got {self.moves_to_go}")

    @property
    def type(self) -> TimeControlType:
        """Return the time control type."""
        return TimeControlType.INCREMENT

    def to_uci_params(self) -> str:
        """Convert to UCI go command parameters.

        Returns:
            UCI parameter string for the go command.
        """
        parts = [
            f"wtime {self.wtime_ms}",
            f"btime {self.btime_ms}",
            f"winc {self.winc_ms}",
            f"binc {self.binc_ms}",
        ]
        if self.moves_to_go is not None:
            parts.append(f"movestogo {self.moves_to_go}")
        return " ".join(parts)


@dataclass(frozen=True)
class DepthTimeControl:
    """Search to a fixed depth.

    Attributes:
        depth: Maximum search depth in plies. Must be positive.
    """

    depth: int

    def __post_init__(self) -> None:
        """Validate depth is positive."""
        if self.depth <= 0:
            raise ValueError(f"depth must be positive, got {self.depth}")

    @property
    def type(self) -> TimeControlType:
        """Return the time control type."""
        return TimeControlType.DEPTH

    def to_uci_params(self) -> str:
        """Convert to UCI go command parameters.

        Returns:
            UCI parameter string for the go command.
        """
        return f"depth {self.depth}"


@dataclass(frozen=True)
class NodesTimeControl:
    """Search a fixed number of nodes.

    Attributes:
        nodes: Maximum number of nodes to search. Must be positive.
    """

    nodes: int

    def __post_init__(self) -> None:
        """Validate nodes is positive."""
        if self.nodes <= 0:
            raise ValueError(f"nodes must be positive, got {self.nodes}")

    @property
    def type(self) -> TimeControlType:
        """Return the time control type."""
        return TimeControlType.NODES

    def to_uci_params(self) -> str:
        """Convert to UCI go command parameters.

        Returns:
            UCI parameter string for the go command.
        """
        return f"nodes {self.nodes}"


TimeControl = FixedTimeControl | IncrementTimeControl | DepthTimeControl | NodesTimeControl


def parse_time_control(tc_str: str) -> TimeControl:
    """Parse a time control specification string.

    Supported formats:
        - ``movetime=1000`` — Fixed time per move in ms.
        - ``depth=10`` — Search to fixed depth.
        - ``nodes=50000`` — Search fixed number of nodes.
        - ``wtime=60000,btime=60000,winc=1000,binc=1000`` — Increment.

    Args:
        tc_str: Time control string.

    Returns:
        Parsed :class:`TimeControl`.

    Raises:
        ValueError: If the format is unrecognised.
    """
    parts = dict(part.split("=", 1) for part in tc_str.split(","))

    if "movetime" in parts:
        return FixedTimeControl(movetime_ms=int(parts["movetime"]))
    if "depth" in parts:
        return DepthTimeControl(depth=int(parts["depth"]))
    if "nodes" in parts:
        return NodesTimeControl(nodes=int(parts["nodes"]))
    if "wtime" in parts and "btime" in parts:
        return IncrementTimeControl(
            wtime_ms=int(parts["wtime"]),
            btime_ms=int(parts["btime"]),
            winc_ms=int(parts.get("winc", "0")),
            binc_ms=int(parts.get("binc", "0")),
        )

    raise ValueError(f"Unknown time control format: {tc_str!r}")
