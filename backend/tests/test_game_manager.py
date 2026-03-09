"""Tests for the game manager service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from backend.services.engine_pool import EnginePool
from backend.services.game_manager import GameManager, GameSession
from shared.storage.models import GameResult
from shared.uci_client import BestMove, UCIInfo, UCIScore


class TestGameManager:
    """Tests for GameManager session lifecycle."""

    @pytest.fixture
    def game_repo(self) -> MagicMock:
        repo = MagicMock()
        repo.save_game = MagicMock()
        return repo

    @pytest.fixture
    def engine_pool(self) -> MagicMock:
        pool = MagicMock(spec=EnginePool)
        pool.acquire = AsyncMock(return_value=MagicMock())
        pool.release = AsyncMock()
        return pool

    @pytest.fixture
    def manager(self, engine_pool: MagicMock, game_repo: MagicMock) -> GameManager:
        return GameManager(engine_pool, game_repo)

    @pytest.mark.asyncio
    async def test_create_session(self, manager: GameManager, engine_pool: MagicMock) -> None:
        session = await manager.create_session(
            engine_id="test-engine",
            engine_path="/path/to/engine",
        )
        assert session.engine_id == "test-engine"
        assert session.player_color == "white"
        assert manager.active_sessions == 1
        engine_pool.acquire.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_session_custom_color(self, manager: GameManager) -> None:
        session = await manager.create_session(
            engine_id="test-engine",
            engine_path="/path/to/engine",
            player_color="black",
        )
        assert session.player_color == "black"

    def test_apply_player_move(self, manager: GameManager) -> None:
        import chess

        session = GameSession(
            game_id="test-1",
            engine_id="test-engine",
            player_color="white",
            board=chess.Board(),
        )

        san, fen = manager.apply_player_move(session, "e2e4")
        assert san == "e4"
        assert "e2e4" not in fen  # FEN doesn't contain UCI
        assert len(session.moves) == 1

    def test_apply_illegal_move_raises(self, manager: GameManager) -> None:
        import chess

        session = GameSession(
            game_id="test-1",
            engine_id="test-engine",
            player_color="white",
            board=chess.Board(),
        )

        with pytest.raises(ValueError, match="Illegal move"):
            manager.apply_player_move(session, "e1e8")

    def test_check_game_over_not_over(self, manager: GameManager) -> None:
        import chess

        session = GameSession(
            game_id="test-1",
            engine_id="test-engine",
            player_color="white",
            board=chess.Board(),
        )
        assert manager.check_game_over(session) is None

    def test_check_game_over_checkmate(self, manager: GameManager) -> None:
        import chess

        # Scholar's mate position
        board = chess.Board()
        for move_str in ["f2f3", "e7e5", "g2g4", "d8h4"]:
            board.push(chess.Move.from_uci(move_str))

        session = GameSession(
            game_id="test-1",
            engine_id="test-engine",
            player_color="white",
            board=board,
        )
        result = manager.check_game_over(session)
        assert result == GameResult.BLACK_WIN

    @pytest.mark.asyncio
    async def test_end_session(
        self,
        manager: GameManager,
        game_repo: MagicMock,
        engine_pool: MagicMock,
    ) -> None:
        session = await manager.create_session(
            engine_id="test-engine",
            engine_path="/path/to/engine",
        )
        game = await manager.end_session(session.game_id, GameResult.DRAW)
        assert game.result == GameResult.DRAW
        assert manager.active_sessions == 0
        game_repo.save_game.assert_called_once()
        engine_pool.release.assert_called_once()

    @pytest.mark.asyncio
    async def test_end_session_unknown_raises(self, manager: GameManager) -> None:
        with pytest.raises(KeyError):
            await manager.end_session("nonexistent", GameResult.DRAW)

    @pytest.mark.asyncio
    async def test_cleanup_session(
        self,
        manager: GameManager,
        engine_pool: MagicMock,
    ) -> None:
        session = await manager.create_session(
            engine_id="test-engine",
            engine_path="/path/to/engine",
        )
        await manager.cleanup_session(session.game_id)
        assert manager.active_sessions == 0
        engine_pool.release.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_nonexistent_is_noop(self, manager: GameManager) -> None:
        await manager.cleanup_session("nonexistent")
        # Should not raise

    @pytest.mark.asyncio
    async def test_make_engine_move(self, manager: GameManager) -> None:
        mock_client = MagicMock()
        mock_client.position = AsyncMock()
        bestmove = BestMove(move="e2e4")
        info = UCIInfo(depth=10, score=UCIScore(cp=30))
        mock_client.go = AsyncMock(return_value=(bestmove, [info]))

        import chess

        session = GameSession(
            game_id="test-1",
            engine_id="test-engine",
            player_color="black",
            board=chess.Board(),
            client=mock_client,
        )

        uci_move, san, _fen, last_info = await manager.make_engine_move(session)
        assert uci_move == "e2e4"
        assert san == "e4"
        assert last_info is not None
        assert last_info.depth == 10
        assert len(session.moves) == 1

    @pytest.mark.asyncio
    async def test_make_engine_move_standard_startpos(self, manager: GameManager) -> None:
        """Standard start sends ``position startpos`` (fen=None)."""
        mock_client = MagicMock()
        mock_client.position = AsyncMock()
        bestmove = BestMove(move="e2e4")
        mock_client.go = AsyncMock(return_value=(bestmove, []))

        import chess

        session = GameSession(
            game_id="test-1",
            engine_id="test-engine",
            player_color="black",
            board=chess.Board(),
            client=mock_client,
        )

        await manager.make_engine_move(session)
        mock_client.position.assert_called_once_with(fen=None, moves=None)

    @pytest.mark.asyncio
    async def test_make_engine_move_custom_fen(self, manager: GameManager) -> None:
        """Custom FEN sends ``position fen <FEN> moves ...`` to engine."""
        custom_fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
        mock_client = MagicMock()
        mock_client.position = AsyncMock()
        # Black to move from this FEN; engine replies e7e5
        bestmove = BestMove(move="e7e5")
        mock_client.go = AsyncMock(return_value=(bestmove, []))

        import chess

        session = GameSession(
            game_id="test-1",
            engine_id="test-engine",
            player_color="white",
            board=chess.Board(custom_fen),
            client=mock_client,
            initial_fen=custom_fen,
        )

        uci_move, san, _fen, _info = await manager.make_engine_move(session)
        assert uci_move == "e7e5"
        assert san == "e5"
        mock_client.position.assert_called_once_with(fen=custom_fen, moves=None)

    @pytest.mark.asyncio
    async def test_create_session_stores_initial_fen(self, manager: GameManager) -> None:
        """create_session stores the initial FEN on the session."""
        custom_fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
        session = await manager.create_session(
            engine_id="test-engine",
            engine_path="/path/to/engine",
            fen=custom_fen,
        )
        assert session.initial_fen == custom_fen

    @pytest.mark.asyncio
    async def test_create_session_standard_start_fen_is_none(self, manager: GameManager) -> None:
        """create_session without FEN leaves initial_fen as None."""
        session = await manager.create_session(
            engine_id="test-engine",
            engine_path="/path/to/engine",
        )
        assert session.initial_fen is None
