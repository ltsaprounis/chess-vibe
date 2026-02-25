"""Tests for time control models."""

import pytest
from shared.time_control import (
    DepthTimeControl,
    FixedTimeControl,
    IncrementTimeControl,
    NodesTimeControl,
    TimeControlType,
)


class TestFixedTimeControl:
    """Tests for FixedTimeControl."""

    def test_valid_creation(self) -> None:
        tc = FixedTimeControl(movetime_ms=1000)
        assert tc.movetime_ms == 1000

    def test_type_property(self) -> None:
        tc = FixedTimeControl(movetime_ms=500)
        assert tc.type == TimeControlType.FIXED_TIME

    def test_to_uci_params(self) -> None:
        tc = FixedTimeControl(movetime_ms=2000)
        assert tc.to_uci_params() == "movetime 2000"

    def test_zero_movetime_raises(self) -> None:
        with pytest.raises(ValueError, match="movetime_ms must be positive"):
            FixedTimeControl(movetime_ms=0)

    def test_negative_movetime_raises(self) -> None:
        with pytest.raises(ValueError, match="movetime_ms must be positive"):
            FixedTimeControl(movetime_ms=-100)

    def test_frozen(self) -> None:
        tc = FixedTimeControl(movetime_ms=1000)
        with pytest.raises(AttributeError):
            tc.movetime_ms = 2000  # type: ignore[misc]


class TestIncrementTimeControl:
    """Tests for IncrementTimeControl."""

    def test_valid_creation_with_defaults(self) -> None:
        tc = IncrementTimeControl(wtime_ms=60000, btime_ms=60000)
        assert tc.wtime_ms == 60000
        assert tc.btime_ms == 60000
        assert tc.winc_ms == 0
        assert tc.binc_ms == 0
        assert tc.moves_to_go is None

    def test_valid_creation_with_increment(self) -> None:
        tc = IncrementTimeControl(wtime_ms=300000, btime_ms=300000, winc_ms=2000, binc_ms=2000)
        assert tc.winc_ms == 2000
        assert tc.binc_ms == 2000

    def test_valid_creation_with_moves_to_go(self) -> None:
        tc = IncrementTimeControl(wtime_ms=60000, btime_ms=60000, moves_to_go=40)
        assert tc.moves_to_go == 40

    def test_type_property(self) -> None:
        tc = IncrementTimeControl(wtime_ms=60000, btime_ms=60000)
        assert tc.type == TimeControlType.INCREMENT

    def test_to_uci_params_without_movestogo(self) -> None:
        tc = IncrementTimeControl(wtime_ms=60000, btime_ms=55000, winc_ms=1000, binc_ms=1000)
        assert tc.to_uci_params() == "wtime 60000 btime 55000 winc 1000 binc 1000"

    def test_to_uci_params_with_movestogo(self) -> None:
        tc = IncrementTimeControl(wtime_ms=60000, btime_ms=60000, moves_to_go=40)
        assert tc.to_uci_params() == "wtime 60000 btime 60000 winc 0 binc 0 movestogo 40"

    def test_negative_wtime_raises(self) -> None:
        with pytest.raises(ValueError, match="wtime_ms must be non-negative"):
            IncrementTimeControl(wtime_ms=-1, btime_ms=60000)

    def test_negative_btime_raises(self) -> None:
        with pytest.raises(ValueError, match="btime_ms must be non-negative"):
            IncrementTimeControl(wtime_ms=60000, btime_ms=-1)

    def test_negative_winc_raises(self) -> None:
        with pytest.raises(ValueError, match="winc_ms must be non-negative"):
            IncrementTimeControl(wtime_ms=60000, btime_ms=60000, winc_ms=-1)

    def test_negative_binc_raises(self) -> None:
        with pytest.raises(ValueError, match="binc_ms must be non-negative"):
            IncrementTimeControl(wtime_ms=60000, btime_ms=60000, binc_ms=-1)

    def test_zero_moves_to_go_raises(self) -> None:
        with pytest.raises(ValueError, match="moves_to_go must be positive"):
            IncrementTimeControl(wtime_ms=60000, btime_ms=60000, moves_to_go=0)

    def test_negative_moves_to_go_raises(self) -> None:
        with pytest.raises(ValueError, match="moves_to_go must be positive"):
            IncrementTimeControl(wtime_ms=60000, btime_ms=60000, moves_to_go=-1)

    def test_zero_time_allowed(self) -> None:
        tc = IncrementTimeControl(wtime_ms=0, btime_ms=0)
        assert tc.wtime_ms == 0
        assert tc.btime_ms == 0

    def test_frozen(self) -> None:
        tc = IncrementTimeControl(wtime_ms=60000, btime_ms=60000)
        with pytest.raises(AttributeError):
            tc.wtime_ms = 0  # type: ignore[misc]


class TestDepthTimeControl:
    """Tests for DepthTimeControl."""

    def test_valid_creation(self) -> None:
        tc = DepthTimeControl(depth=20)
        assert tc.depth == 20

    def test_type_property(self) -> None:
        tc = DepthTimeControl(depth=10)
        assert tc.type == TimeControlType.DEPTH

    def test_to_uci_params(self) -> None:
        tc = DepthTimeControl(depth=15)
        assert tc.to_uci_params() == "depth 15"

    def test_zero_depth_raises(self) -> None:
        with pytest.raises(ValueError, match="depth must be positive"):
            DepthTimeControl(depth=0)

    def test_negative_depth_raises(self) -> None:
        with pytest.raises(ValueError, match="depth must be positive"):
            DepthTimeControl(depth=-5)

    def test_frozen(self) -> None:
        tc = DepthTimeControl(depth=10)
        with pytest.raises(AttributeError):
            tc.depth = 20  # type: ignore[misc]


class TestNodesTimeControl:
    """Tests for NodesTimeControl."""

    def test_valid_creation(self) -> None:
        tc = NodesTimeControl(nodes=1000000)
        assert tc.nodes == 1000000

    def test_type_property(self) -> None:
        tc = NodesTimeControl(nodes=500000)
        assert tc.type == TimeControlType.NODES

    def test_to_uci_params(self) -> None:
        tc = NodesTimeControl(nodes=100000)
        assert tc.to_uci_params() == "nodes 100000"

    def test_zero_nodes_raises(self) -> None:
        with pytest.raises(ValueError, match="nodes must be positive"):
            NodesTimeControl(nodes=0)

    def test_negative_nodes_raises(self) -> None:
        with pytest.raises(ValueError, match="nodes must be positive"):
            NodesTimeControl(nodes=-1)

    def test_frozen(self) -> None:
        tc = NodesTimeControl(nodes=100000)
        with pytest.raises(AttributeError):
            tc.nodes = 200000  # type: ignore[misc]
