"""Flat-file storage implementation for the chess-vibe persistence layer.

Directory layout::

    data/
      sprt-tests/{test-id}/meta.json
      sprt-tests/{test-id}/games/{game-id}.pgn
      sprt-tests/{test-id}/games/{game-id}.eval.json
      play/{game-id}.pgn
      play/{game-id}.eval.json
      openings/{book-id}.{pgn,epd}
      openings/{book-id}.meta.json

Atomic writes are achieved via temp-file + ``os.replace``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from shared.storage.models import (
    Game,
    GameFilter,
    GameResult,
    Move,
    OpeningBook,
    SPRTOutcome,
    SPRTStatus,
    SPRTTest,
    SPRTTestFilter,
)
from shared.storage.pgn_export import export_game_to_pgn
from shared.storage.repository import GameRepository, OpeningBookRepository, SPRTTestRepository
from shared.time_control import (
    DepthTimeControl,
    FixedTimeControl,
    IncrementTimeControl,
    NodesTimeControl,
    TimeControl,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _serialize_time_control(tc: TimeControl) -> dict[str, Any]:
    """Convert a TimeControl to a JSON-serialisable dict."""
    if isinstance(tc, FixedTimeControl):
        return {"type": "fixed_time", "movetime_ms": tc.movetime_ms}
    if isinstance(tc, IncrementTimeControl):
        return {
            "type": "increment",
            "wtime_ms": tc.wtime_ms,
            "btime_ms": tc.btime_ms,
            "winc_ms": tc.winc_ms,
            "binc_ms": tc.binc_ms,
            "moves_to_go": tc.moves_to_go,
        }
    if isinstance(tc, DepthTimeControl):
        return {"type": "depth", "depth": tc.depth}
    # NodesTimeControl is the only remaining variant.
    return {"type": "nodes", "nodes": tc.nodes}


def _deserialize_time_control(data: dict[str, Any]) -> TimeControl:
    """Reconstruct a TimeControl from a JSON dict."""
    tc_type: str = data["type"]
    if tc_type == "fixed_time":
        return FixedTimeControl(movetime_ms=data["movetime_ms"])
    if tc_type == "increment":
        return IncrementTimeControl(
            wtime_ms=data["wtime_ms"],
            btime_ms=data["btime_ms"],
            winc_ms=data["winc_ms"],
            binc_ms=data["binc_ms"],
            moves_to_go=data.get("moves_to_go"),
        )
    if tc_type == "depth":
        return DepthTimeControl(depth=data["depth"])
    if tc_type == "nodes":
        return NodesTimeControl(nodes=data["nodes"])
    raise ValueError(f"Unknown time control type: {tc_type!r}")


def _serialize_move(move: Move) -> dict[str, Any]:
    """Convert a Move to a JSON-serialisable dict."""
    return {
        "uci": move.uci,
        "san": move.san,
        "fen_after": move.fen_after,
        "score_cp": move.score_cp,
        "score_mate": move.score_mate,
        "depth": move.depth,
        "seldepth": move.seldepth,
        "pv": move.pv,
        "nodes": move.nodes,
        "time_ms": move.time_ms,
        "clock_white_ms": move.clock_white_ms,
        "clock_black_ms": move.clock_black_ms,
    }


def _deserialize_move(data: dict[str, Any]) -> Move:
    """Reconstruct a Move from a JSON dict."""
    return Move(
        uci=data["uci"],
        san=data["san"],
        fen_after=data["fen_after"],
        score_cp=data.get("score_cp"),
        score_mate=data.get("score_mate"),
        depth=data.get("depth"),
        seldepth=data.get("seldepth"),
        pv=data.get("pv", []),
        nodes=data.get("nodes"),
        time_ms=data.get("time_ms"),
        clock_white_ms=data.get("clock_white_ms"),
        clock_black_ms=data.get("clock_black_ms"),
    )


def _serialize_game(game: Game) -> dict[str, Any]:
    """Convert a Game to a JSON-serialisable dict."""
    return {
        "id": game.id,
        "white_engine": game.white_engine,
        "black_engine": game.black_engine,
        "result": game.result.value,
        "opening_name": game.opening_name,
        "sprt_test_id": game.sprt_test_id,
        "start_fen": game.start_fen,
        "time_control": (_serialize_time_control(game.time_control) if game.time_control else None),
        "created_at": game.created_at.isoformat(),
        "moves": [_serialize_move(m) for m in game.moves],
    }


def _deserialize_game(data: dict[str, Any]) -> Game:
    """Reconstruct a Game from a JSON dict."""
    tc_raw: dict[str, Any] | None = data.get("time_control")
    return Game(
        id=data["id"],
        white_engine=data["white_engine"],
        black_engine=data["black_engine"],
        result=GameResult(data["result"]),
        moves=[_deserialize_move(m) for m in data["moves"]],
        created_at=datetime.fromisoformat(data["created_at"]),
        opening_name=data.get("opening_name"),
        sprt_test_id=data.get("sprt_test_id"),
        start_fen=data.get("start_fen"),
        time_control=_deserialize_time_control(tc_raw) if tc_raw else None,
    )


def _serialize_sprt_test(test: SPRTTest) -> dict[str, Any]:
    """Convert an SPRTTest to a JSON-serialisable dict."""
    return {
        "id": test.id,
        "engine_a": test.engine_a,
        "engine_b": test.engine_b,
        "time_control": _serialize_time_control(test.time_control),
        "elo0": test.elo0,
        "elo1": test.elo1,
        "alpha": test.alpha,
        "beta": test.beta,
        "created_at": test.created_at.isoformat(),
        "status": test.status.value,
        "wins": test.wins,
        "losses": test.losses,
        "draws": test.draws,
        "llr": test.llr,
        "result": test.result.value if test.result else None,
        "completed_at": test.completed_at.isoformat() if test.completed_at else None,
    }


def _deserialize_sprt_test(data: dict[str, Any]) -> SPRTTest:
    """Reconstruct an SPRTTest from a JSON dict."""
    result_raw: str | None = data.get("result")
    completed_raw: str | None = data.get("completed_at")
    return SPRTTest(
        id=data["id"],
        engine_a=data["engine_a"],
        engine_b=data["engine_b"],
        time_control=_deserialize_time_control(data["time_control"]),
        elo0=data["elo0"],
        elo1=data["elo1"],
        alpha=data["alpha"],
        beta=data["beta"],
        created_at=datetime.fromisoformat(data["created_at"]),
        status=SPRTStatus(data["status"]),
        wins=data["wins"],
        losses=data["losses"],
        draws=data["draws"],
        llr=data["llr"],
        result=SPRTOutcome(result_raw) if result_raw else None,
        completed_at=datetime.fromisoformat(completed_raw) if completed_raw else None,
    )


# ---------------------------------------------------------------------------
# Atomic file I/O
# ---------------------------------------------------------------------------


def _atomic_write(path: Path, content: str) -> None:
    """Write *content* to *path* atomically (temp file + rename).

    Parent directories are created as needed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_path, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


def _read_json(path: Path) -> Any:
    """Read and parse a JSON file."""
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Game filter helper
# ---------------------------------------------------------------------------


def _matches_game_filter(game: Game, game_filter: GameFilter) -> bool:
    """Return ``True`` if *game* satisfies all criteria in *game_filter*."""
    if game_filter.sprt_test_id is not None and game.sprt_test_id != game_filter.sprt_test_id:
        return False
    if game_filter.result is not None and game.result != game_filter.result:
        return False
    if game_filter.engine_id is not None and game_filter.engine_id not in (
        game.white_engine,
        game.black_engine,
    ):
        return False
    if (  # noqa: SIM103
        game_filter.opening_name is not None and game.opening_name != game_filter.opening_name
    ):
        return False
    return True


def _matches_sprt_filter(test: SPRTTest, test_filter: SPRTTestFilter) -> bool:
    """Return ``True`` if *test* satisfies all criteria in *test_filter*."""
    if test_filter.status is not None and test.status != test_filter.status:
        return False
    if (  # noqa: SIM103
        test_filter.engine_id is not None
        and test_filter.engine_id not in (test.engine_a, test.engine_b)
    ):
        return False
    return True


# ---------------------------------------------------------------------------
# FileGameRepository
# ---------------------------------------------------------------------------


class FileGameRepository(GameRepository):
    """Flat-file implementation of :class:`GameRepository`.

    Games belonging to an SPRT test are stored under
    ``data_dir/sprt-tests/{test-id}/games/``.  Standalone games live under
    ``data_dir/play/``.

    Args:
        data_dir: Root data directory (e.g. ``Path("data")``).
    """

    def __init__(self, data_dir: Path) -> None:
        """Initialise with the root data directory."""
        self._data_dir = data_dir

    # -- helpers ----------------------------------------------------------

    def _game_dir(self, game: Game) -> Path:
        """Return the directory that should hold *game*'s files."""
        if game.sprt_test_id is not None:
            return self._data_dir / "sprt-tests" / game.sprt_test_id / "games"
        return self._data_dir / "play"

    def _eval_path(self, directory: Path, game_id: str) -> Path:
        return directory / f"{game_id}.eval.json"

    def _pgn_path(self, directory: Path, game_id: str) -> Path:
        return directory / f"{game_id}.pgn"

    # -- public API -------------------------------------------------------

    def save_game(self, game: Game) -> None:
        """Persist a game as a ``.eval.json`` and ``.pgn`` pair."""
        directory = self._game_dir(game)
        eval_path = self._eval_path(directory, game.id)
        pgn_path = self._pgn_path(directory, game.id)

        eval_json = json.dumps(_serialize_game(game), indent=2)
        _atomic_write(eval_path, eval_json)

        pgn_text = export_game_to_pgn(game)
        _atomic_write(pgn_path, pgn_text)

        logger.debug("Saved game %s to %s", game.id, directory)

    def get_game(self, game_id: str) -> Game | None:
        """Load a game by ID, searching ``play/`` then all SPRT test dirs."""
        # Check play/ first.
        play_eval = self._eval_path(self._data_dir / "play", game_id)
        if play_eval.is_file():
            return _deserialize_game(_read_json(play_eval))

        # Check every SPRT test directory.
        sprt_root = self._data_dir / "sprt-tests"
        if sprt_root.is_dir():
            for test_dir in sprt_root.iterdir():
                if not test_dir.is_dir():
                    continue
                eval_path = self._eval_path(test_dir / "games", game_id)
                if eval_path.is_file():
                    return _deserialize_game(_read_json(eval_path))

        return None

    def list_games(self, game_filter: GameFilter | None = None) -> list[Game]:
        """List games, optionally filtered."""
        games: list[Game] = []

        # Determine which directories to scan.
        dirs_to_scan: list[Path] = []

        if game_filter and game_filter.sprt_test_id is not None:
            # Only look in the specific test's games directory.
            dirs_to_scan.append(self._data_dir / "sprt-tests" / game_filter.sprt_test_id / "games")
        else:
            # Scan play/ and all SPRT test game directories.
            play_dir = self._data_dir / "play"
            if play_dir.is_dir():
                dirs_to_scan.append(play_dir)
            sprt_root = self._data_dir / "sprt-tests"
            if sprt_root.is_dir():
                for test_dir in sprt_root.iterdir():
                    games_dir = test_dir / "games"
                    if games_dir.is_dir():
                        dirs_to_scan.append(games_dir)

        for directory in dirs_to_scan:
            if not directory.is_dir():
                continue
            for eval_file in directory.glob("*.eval.json"):
                game = _deserialize_game(_read_json(eval_file))
                if game_filter is None or _matches_game_filter(game, game_filter):
                    games.append(game)

        return games


# ---------------------------------------------------------------------------
# FileSPRTTestRepository
# ---------------------------------------------------------------------------


class FileSPRTTestRepository(SPRTTestRepository):
    """Flat-file implementation of :class:`SPRTTestRepository`.

    Test metadata is stored at ``data_dir/sprt-tests/{test-id}/meta.json``.

    Args:
        data_dir: Root data directory (e.g. ``Path("data")``).
    """

    def __init__(self, data_dir: Path) -> None:
        """Initialise with the root data directory."""
        self._data_dir = data_dir

    # -- helpers ----------------------------------------------------------

    def _meta_path(self, test_id: str) -> Path:
        return self._data_dir / "sprt-tests" / test_id / "meta.json"

    # -- public API -------------------------------------------------------

    def save_sprt_test(self, test: SPRTTest) -> None:
        """Persist an SPRT test as ``meta.json``."""
        path = self._meta_path(test.id)
        content = json.dumps(_serialize_sprt_test(test), indent=2)
        _atomic_write(path, content)
        logger.debug("Saved SPRT test %s to %s", test.id, path)

    def get_sprt_test(self, test_id: str) -> SPRTTest | None:
        """Load an SPRT test by ID."""
        path = self._meta_path(test_id)
        if not path.is_file():
            return None
        return _deserialize_sprt_test(_read_json(path))

    def list_sprt_tests(self, test_filter: SPRTTestFilter | None = None) -> list[SPRTTest]:
        """List SPRT tests, optionally filtered."""
        tests: list[SPRTTest] = []
        sprt_root = self._data_dir / "sprt-tests"
        if not sprt_root.is_dir():
            return tests

        for test_dir in sprt_root.iterdir():
            meta_path = test_dir / "meta.json"
            if not meta_path.is_file():
                continue
            test = _deserialize_sprt_test(_read_json(meta_path))
            if test_filter is None or _matches_sprt_filter(test, test_filter):
                tests.append(test)

        return tests

    def update_sprt_results(self, test: SPRTTest) -> None:
        """Update an existing SPRT test (must already exist).

        Raises:
            KeyError: If no test with the given ``id`` exists.
        """
        path = self._meta_path(test.id)
        if not path.is_file():
            raise KeyError(f"SPRT test '{test.id}' not found")
        content = json.dumps(_serialize_sprt_test(test), indent=2)
        _atomic_write(path, content)
        logger.debug("Updated SPRT test %s", test.id)


# ---------------------------------------------------------------------------
# FileOpeningBookRepository
# ---------------------------------------------------------------------------


class FileOpeningBookRepository(OpeningBookRepository):
    """Flat-file implementation of :class:`OpeningBookRepository`.

    Opening books are stored at ``data_dir/openings/{book-id}.{format}``,
    where ``book-id`` is a generated UUID. The uploaded filename is kept in
    a ``{book-id}.meta.json`` sidecar rather than in the filename itself, so
    that a book's ``id`` stays an opaque identifier that both
    :meth:`list_books` and :meth:`get_book_path` derive from the file stem.

    Args:
        data_dir: Root data directory (e.g. ``Path("data")``).
    """

    _SUPPORTED_EXTENSIONS = frozenset({".pgn", ".epd"})
    _METADATA_SUFFIX = ".meta.json"

    def __init__(self, data_dir: Path) -> None:
        """Initialise with the root data directory."""
        self._books_dir = data_dir / "openings"

    def list_books(self) -> list[OpeningBook]:
        """List all available opening books.

        A book's id is its filename stem, so two supported extensions
        sharing a stem (``dup.pgn`` / ``dup.epd``) would describe one id.
        Only the first is listed, since only the first is fetchable: ids
        must be unique for :meth:`get_book_path` to mean anything, and the
        frontend keys its dropdown on them.
        """
        books: list[OpeningBook] = []
        seen: set[str] = set()
        for path in self._book_files():
            book_id = path.stem
            if book_id in seen:
                logger.warning(
                    "Ignoring opening book %s%s: id %s is already taken",
                    book_id,
                    path.suffix,
                    book_id,
                )
                continue
            seen.add(book_id)
            books.append(
                OpeningBook(
                    id=book_id,
                    name=self._read_display_name(book_id) or book_id,
                    path=str(path),
                    format=path.suffix.lstrip("."),
                )
            )
        return books

    def save_book(self, name: str, content: bytes, format: str) -> OpeningBook:
        """Persist an opening book file and its display name.

        Raises:
            ValueError: If *format* is not a supported book extension.
        """
        if f".{format}" not in self._SUPPORTED_EXTENSIONS:
            raise ValueError(f"Unsupported opening book format: {format!r}")

        self._books_dir.mkdir(parents=True, exist_ok=True)

        book_id = str(uuid.uuid4())
        dest = self._books_dir / f"{book_id}.{format}"
        # Sidecar first: a save that fails half way leaves an orphan sidecar,
        # which neither reader can see, rather than an orphan book file, which
        # would list under its UUID — the very symptom this sidecar fixes.
        _atomic_write(self._metadata_path(book_id), json.dumps({"name": name}))
        dest.write_bytes(content)

        logger.debug("Saved opening book %s to %s", book_id, dest)
        return OpeningBook(
            id=book_id,
            name=name,
            path=str(dest),
            format=format,
        )

    def get_book_path(self, book_id: str) -> Path | None:
        """Retrieve the filesystem path for a book by its ID."""
        return next((path for path in self._book_files() if path.stem == book_id), None)

    def _book_files(self) -> list[Path]:
        """The book files, filtered and ordered.

        Both readers derive from this, so the file :meth:`get_book_path`
        resolves for an id is by construction the one :meth:`list_books`
        reported under it. Sorting matters because directory order is
        arbitrary and a stem can be ambiguous; filtering by extension keeps
        sidecars, ``_atomic_write`` temp files, and anything else in the
        directory from being mistaken for a book.
        """
        if not self._books_dir.is_dir():
            return []
        return [
            path
            for path in sorted(self._books_dir.iterdir())
            if path.is_file() and path.suffix in self._SUPPORTED_EXTENSIONS
        ]

    def _metadata_path(self, book_id: str) -> Path:
        """Path of the sidecar holding *book_id*'s display name."""
        return self._books_dir / f"{book_id}{self._METADATA_SUFFIX}"

    def _read_display_name(self, book_id: str) -> str | None:
        """Read a book's stored display name.

        Returns ``None`` — leaving the caller to fall back to the book id —
        when the sidecar is missing, unreadable, not JSON, or carries no
        usable ``name``. Books written before sidecars existed have no
        sidecar, and a half-written one must not take the whole listing down.

        An absent sidecar is the ordinary legacy case and is silent. One that
        exists but cannot be used is logged: the visible symptom is a book
        reverting to its UUID name, which is exactly the bug the sidecar was
        introduced to fix, so it should not be diagnosed by guesswork.
        """
        path = self._metadata_path(book_id)
        if not path.is_file():
            return None
        try:
            data: Any = _read_json(path)
            if isinstance(data, dict):
                name: Any = cast("dict[str, Any]", data).get("name")
                if isinstance(name, str):
                    return name
        except (OSError, ValueError):
            logger.warning("Unreadable opening book sidecar for %s", book_id, exc_info=True)
            return None
        logger.warning("Opening book sidecar for %s has no usable name", book_id)
        return None
