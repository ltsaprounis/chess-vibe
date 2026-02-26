"""Game session manager for play-vs-engine games.

Maintains active WebSocket game sessions, delegates engine I/O to
the engine pool, and persists completed games via the repository.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import chess
from shared.storage.models import Game, GameResult, Move
from shared.storage.repository import GameRepository
from shared.time_control import FixedTimeControl, TimeControl
from shared.uci_client import UCIClient, UCIInfo

from backend.services.engine_pool import EnginePool

logger = logging.getLogger(__name__)

_DEFAULT_MOVETIME_MS = 1000


@dataclass
class GameSession:
    """State for an active play-vs-engine game session.

    Attributes:
        game_id: Unique game identifier.
        engine_id: Registry ID of the engine.
        player_color: ``"white"`` or ``"black"``.
        board: Current board state.
        moves: Recorded moves so far.
        client: UCI engine client.
        time_control: Time control for the engine.
        created_at: Timestamp when the session started.
    """

    game_id: str
    engine_id: str
    player_color: str
    board: chess.Board
    moves: list[Move] = field(default_factory=list[Move])
    client: UCIClient | None = None
    time_control: TimeControl = field(
        default_factory=lambda: FixedTimeControl(movetime_ms=_DEFAULT_MOVETIME_MS)
    )
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class GameManager:
    """Manages active play-vs-engine game sessions.

    Coordinates between WebSocket handlers, the engine pool, and the
    game repository to run interactive games.
    """

    def __init__(
        self,
        engine_pool: EnginePool,
        game_repo: GameRepository,
    ) -> None:
        """Initialise the game manager.

        Args:
            engine_pool: Pool for borrowing engine subprocesses.
            game_repo: Repository for persisting completed games.
        """
        self._engine_pool = engine_pool
        self._game_repo = game_repo
        self._sessions: dict[str, GameSession] = {}

    @property
    def active_sessions(self) -> int:
        """Number of currently active game sessions."""
        return len(self._sessions)

    async def create_session(
        self,
        engine_id: str,
        engine_path: str,
        *,
        player_color: str = "white",
        time_control: TimeControl | None = None,
        fen: str | None = None,
    ) -> GameSession:
        """Create a new game session and acquire an engine.

        Args:
            engine_id: Registry identifier for the engine.
            engine_path: Filesystem path to the engine executable.
            player_color: ``"white"`` or ``"black"``.
            time_control: Time control for engine moves.
            fen: Starting FEN, or ``None`` for standard start.

        Returns:
            The created :class:`GameSession`.
        """
        game_id = str(uuid.uuid4())
        board = chess.Board(fen) if fen else chess.Board()
        tc = time_control or FixedTimeControl(movetime_ms=_DEFAULT_MOVETIME_MS)

        client = await self._engine_pool.acquire(engine_path)

        session = GameSession(
            game_id=game_id,
            engine_id=engine_id,
            player_color=player_color,
            board=board,
            client=client,
            time_control=tc,
        )
        self._sessions[game_id] = session

        logger.info("Created game session %s (engine=%s)", game_id, engine_id)
        return session

    def get_session(self, game_id: str) -> GameSession | None:
        """Get an active session by game ID."""
        return self._sessions.get(game_id)

    async def make_engine_move(
        self,
        session: GameSession,
    ) -> tuple[str, str, str, UCIInfo | None]:
        """Ask the engine to make a move.

        Args:
            session: The active game session.

        Returns:
            Tuple of (UCI move, SAN move, FEN after, last UCIInfo or None).
        """
        if session.client is None:
            raise RuntimeError("No engine client for session")

        moves_uci = [m.uci for m in session.moves]
        await session.client.position(moves=moves_uci if moves_uci else None)

        bestmove, infos = await session.client.go(session.time_control)

        move_obj = chess.Move.from_uci(bestmove.move)
        san = session.board.san(move_obj)
        session.board.push(move_obj)
        fen_after = session.board.fen()

        last_info = infos[-1] if infos else None

        move_record = Move(
            uci=bestmove.move,
            san=san,
            fen_after=fen_after,
            score_cp=last_info.score.cp if last_info and last_info.score else None,
            score_mate=last_info.score.mate if last_info and last_info.score else None,
            depth=last_info.depth if last_info else None,
            seldepth=last_info.seldepth if last_info else None,
            pv=last_info.pv if last_info else [],
            nodes=last_info.nodes if last_info else None,
            time_ms=last_info.time_ms if last_info else None,
        )
        session.moves.append(move_record)

        return bestmove.move, san, fen_after, last_info

    def apply_player_move(
        self,
        session: GameSession,
        move_uci: str,
    ) -> tuple[str, str]:
        """Apply a player's move to the session.

        Args:
            session: The active game session.
            move_uci: Move in UCI notation.

        Returns:
            Tuple of (SAN move, FEN after).

        Raises:
            ValueError: If the move is illegal.
        """
        try:
            move_obj = chess.Move.from_uci(move_uci)
        except ValueError as e:
            raise ValueError(f"Invalid UCI move: {move_uci}") from e

        if move_obj not in session.board.legal_moves:
            raise ValueError(f"Illegal move: {move_uci}")

        san = session.board.san(move_obj)
        session.board.push(move_obj)
        fen_after = session.board.fen()

        move_record = Move(
            uci=move_uci,
            san=san,
            fen_after=fen_after,
        )
        session.moves.append(move_record)

        return san, fen_after

    def check_game_over(self, session: GameSession) -> GameResult | None:
        """Check if the game is over.

        Returns:
            The result if the game is over, ``None`` otherwise.
        """
        if not session.board.is_game_over():
            return None

        outcome = session.board.outcome()
        if outcome is None:
            return GameResult.DRAW

        if outcome.winner is None:
            return GameResult.DRAW
        if outcome.winner:
            return GameResult.WHITE_WIN
        return GameResult.BLACK_WIN

    async def end_session(
        self,
        game_id: str,
        result: GameResult,
    ) -> Game:
        """End a game session, persist the game, and release the engine.

        Args:
            game_id: The game session ID.
            result: Final game result.

        Returns:
            The persisted :class:`Game` record.
        """
        session = self._sessions.pop(game_id, None)
        if session is None:
            raise KeyError(f"No active session: {game_id}")

        game = Game(
            id=session.game_id,
            white_engine=(session.engine_id if session.player_color == "black" else "player"),
            black_engine=(session.engine_id if session.player_color == "white" else "player"),
            result=result,
            moves=session.moves,
            created_at=session.created_at,
            time_control=session.time_control,
        )
        self._game_repo.save_game(game)

        if session.client is not None:
            await self._engine_pool.release(session.client)

        logger.info("Ended game session %s with result %s", game_id, result.value)
        return game

    async def cleanup_session(self, game_id: str) -> None:
        """Clean up a session without persisting (e.g. on disconnect).

        Args:
            game_id: The game session ID.
        """
        session = self._sessions.pop(game_id, None)
        if session is None:
            return

        if session.client is not None:
            await self._engine_pool.release(session.client)

        logger.info("Cleaned up session %s", game_id)
