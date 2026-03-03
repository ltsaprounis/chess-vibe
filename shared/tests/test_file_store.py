"""Tests for the flat-file storage implementation."""

from __future__ import annotations

import concurrent.futures
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest
from shared.storage.file_store import (
    FileGameRepository,
    FileOpeningBookRepository,
    FileSPRTTestRepository,
)
from shared.storage.models import (
    Game,
    GameFilter,
    GameResult,
    Move,
    SPRTOutcome,
    SPRTStatus,
    SPRTTest,
    SPRTTestFilter,
)
from shared.time_control import DepthTimeControl, FixedTimeControl, IncrementTimeControl

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_move(uci: str = "e2e4", san: str = "e4", **kwargs: object) -> Move:
    """Create a Move with sensible defaults."""
    defaults: dict[str, object] = {
        "fen_after": "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
    }
    defaults.update(kwargs)
    return Move(uci=uci, san=san, **defaults)  # type: ignore[arg-type]


def _make_game(
    game_id: str = "game-001",
    *,
    white: str = "engine-a",
    black: str = "engine-b",
    result: GameResult = GameResult.WHITE_WIN,
    sprt_test_id: str | None = None,
    opening_name: str | None = None,
    moves: list[Move] | None = None,
    time_control: FixedTimeControl | IncrementTimeControl | DepthTimeControl | None = None,
) -> Game:
    """Create a Game with sensible defaults."""
    return Game(
        id=game_id,
        white_engine=white,
        black_engine=black,
        result=result,
        moves=moves or [_make_move()],
        created_at=datetime(2025, 1, 15, 12, 0, 0),
        opening_name=opening_name,
        sprt_test_id=sprt_test_id,
        time_control=time_control,
    )


def _make_sprt_test(
    test_id: str = "test-001",
    *,
    engine_a: str = "engine-a",
    engine_b: str = "engine-b",
    status: SPRTStatus = SPRTStatus.RUNNING,
) -> SPRTTest:
    """Create an SPRTTest with sensible defaults."""
    return SPRTTest(
        id=test_id,
        engine_a=engine_a,
        engine_b=engine_b,
        time_control=FixedTimeControl(movetime_ms=1000),
        elo0=0.0,
        elo1=5.0,
        alpha=0.05,
        beta=0.05,
        created_at=datetime(2025, 1, 15, 12, 0, 0),
        status=status,
    )


# ---------------------------------------------------------------------------
# FileGameRepository
# ---------------------------------------------------------------------------


class TestFileGameRepositoryRoundTrip:
    """Round-trip save/load tests for games."""

    def test_save_and_get_standalone_game(self, tmp_path: Path) -> None:
        repo = FileGameRepository(tmp_path)
        game = _make_game()
        repo.save_game(game)

        loaded = repo.get_game("game-001")
        assert loaded is not None
        assert loaded.id == game.id
        assert loaded.white_engine == game.white_engine
        assert loaded.black_engine == game.black_engine
        assert loaded.result == game.result
        assert len(loaded.moves) == 1
        assert loaded.moves[0].uci == "e2e4"

    def test_save_and_get_sprt_game(self, tmp_path: Path) -> None:
        repo = FileGameRepository(tmp_path)
        game = _make_game(sprt_test_id="test-001")
        repo.save_game(game)

        loaded = repo.get_game("game-001")
        assert loaded is not None
        assert loaded.sprt_test_id == "test-001"

    def test_get_nonexistent_returns_none(self, tmp_path: Path) -> None:
        repo = FileGameRepository(tmp_path)
        assert repo.get_game("nonexistent") is None

    def test_round_trip_preserves_eval_fields(self, tmp_path: Path) -> None:
        move = Move(
            uci="e2e4",
            san="e4",
            fen_after="fen",
            score_cp=35,
            score_mate=None,
            depth=20,
            seldepth=25,
            pv=["e7e5", "g1f3"],
            nodes=1000000,
            time_ms=500,
            clock_white_ms=59500,
            clock_black_ms=60000,
        )
        game = _make_game(moves=[move])
        repo = FileGameRepository(tmp_path)
        repo.save_game(game)

        loaded = repo.get_game("game-001")
        assert loaded is not None
        m = loaded.moves[0]
        assert m.score_cp == 35
        assert m.score_mate is None
        assert m.depth == 20
        assert m.seldepth == 25
        assert m.pv == ["e7e5", "g1f3"]
        assert m.nodes == 1000000
        assert m.time_ms == 500
        assert m.clock_white_ms == 59500
        assert m.clock_black_ms == 60000

    def test_round_trip_preserves_time_control(self, tmp_path: Path) -> None:
        tc = IncrementTimeControl(wtime_ms=60000, btime_ms=60000, winc_ms=1000, binc_ms=1000)
        game = _make_game(time_control=tc)
        repo = FileGameRepository(tmp_path)
        repo.save_game(game)

        loaded = repo.get_game("game-001")
        assert loaded is not None
        assert loaded.time_control == tc

    def test_round_trip_preserves_depth_time_control(self, tmp_path: Path) -> None:
        tc = DepthTimeControl(depth=10)
        game = _make_game(time_control=tc)
        repo = FileGameRepository(tmp_path)
        repo.save_game(game)

        loaded = repo.get_game("game-001")
        assert loaded is not None
        assert loaded.time_control == tc

    def test_round_trip_preserves_mate_score(self, tmp_path: Path) -> None:
        move = _make_move(score_mate=3)
        game = _make_game(moves=[move])
        repo = FileGameRepository(tmp_path)
        repo.save_game(game)

        loaded = repo.get_game("game-001")
        assert loaded is not None
        assert loaded.moves[0].score_mate == 3

    def test_creates_pgn_file(self, tmp_path: Path) -> None:
        repo = FileGameRepository(tmp_path)
        game = _make_game()
        repo.save_game(game)

        pgn_path = tmp_path / "play" / "game-001.pgn"
        assert pgn_path.is_file()
        content = pgn_path.read_text()
        assert "[White" in content
        assert "1-0" in content

    def test_creates_eval_json_file(self, tmp_path: Path) -> None:
        repo = FileGameRepository(tmp_path)
        game = _make_game()
        repo.save_game(game)

        eval_path = tmp_path / "play" / "game-001.eval.json"
        assert eval_path.is_file()

    def test_sprt_game_stored_in_test_directory(self, tmp_path: Path) -> None:
        repo = FileGameRepository(tmp_path)
        game = _make_game(sprt_test_id="test-42")
        repo.save_game(game)

        eval_path = tmp_path / "sprt-tests" / "test-42" / "games" / "game-001.eval.json"
        assert eval_path.is_file()

    def test_overwrite_existing_game(self, tmp_path: Path) -> None:
        repo = FileGameRepository(tmp_path)
        game1 = _make_game(result=GameResult.WHITE_WIN)
        repo.save_game(game1)

        game2 = _make_game(result=GameResult.DRAW)
        repo.save_game(game2)

        loaded = repo.get_game("game-001")
        assert loaded is not None
        assert loaded.result == GameResult.DRAW


# ---------------------------------------------------------------------------
# Game filtering
# ---------------------------------------------------------------------------


class TestFileGameRepositoryFilters:
    """Filter correctness tests for listing games."""

    @pytest.fixture()
    def repo_with_games(self, tmp_path: Path) -> FileGameRepository:
        """Create a repository with several games for filter testing."""
        repo = FileGameRepository(tmp_path)
        games = [
            _make_game(
                "g1",
                white="ea",
                black="eb",
                result=GameResult.WHITE_WIN,
                sprt_test_id="t1",
                opening_name="Sicilian",
            ),
            _make_game(
                "g2",
                white="ea",
                black="ec",
                result=GameResult.BLACK_WIN,
                sprt_test_id="t1",
                opening_name="French",
            ),
            _make_game("g3", white="eb", black="ec", result=GameResult.DRAW, sprt_test_id="t2"),
            _make_game("g4", white="ea", black="eb", result=GameResult.WHITE_WIN),
        ]
        for g in games:
            repo.save_game(g)
        return repo

    def test_list_all(self, repo_with_games: FileGameRepository) -> None:
        games = repo_with_games.list_games()
        assert len(games) == 4

    def test_filter_by_sprt_test_id(self, repo_with_games: FileGameRepository) -> None:
        games = repo_with_games.list_games(GameFilter(sprt_test_id="t1"))
        assert len(games) == 2
        assert all(g.sprt_test_id == "t1" for g in games)

    def test_filter_by_result(self, repo_with_games: FileGameRepository) -> None:
        games = repo_with_games.list_games(GameFilter(result=GameResult.WHITE_WIN))
        assert len(games) == 2
        assert all(g.result == GameResult.WHITE_WIN for g in games)

    def test_filter_by_engine_id(self, repo_with_games: FileGameRepository) -> None:
        games = repo_with_games.list_games(GameFilter(engine_id="ec"))
        ids = {g.id for g in games}
        assert ids == {"g2", "g3"}

    def test_filter_by_opening_name(self, repo_with_games: FileGameRepository) -> None:
        games = repo_with_games.list_games(GameFilter(opening_name="Sicilian"))
        assert len(games) == 1
        assert games[0].id == "g1"

    def test_combined_filter(self, repo_with_games: FileGameRepository) -> None:
        games = repo_with_games.list_games(
            GameFilter(sprt_test_id="t1", result=GameResult.WHITE_WIN)
        )
        assert len(games) == 1
        assert games[0].id == "g1"

    def test_no_matches(self, repo_with_games: FileGameRepository) -> None:
        games = repo_with_games.list_games(GameFilter(engine_id="nonexistent"))
        assert games == []


# ---------------------------------------------------------------------------
# FileSPRTTestRepository
# ---------------------------------------------------------------------------


class TestFileSPRTTestRepositoryRoundTrip:
    """Round-trip save/load tests for SPRT tests."""

    def test_save_and_get(self, tmp_path: Path) -> None:
        repo = FileSPRTTestRepository(tmp_path)
        test = _make_sprt_test()
        repo.save_sprt_test(test)

        loaded = repo.get_sprt_test("test-001")
        assert loaded is not None
        assert loaded.id == test.id
        assert loaded.engine_a == test.engine_a
        assert loaded.engine_b == test.engine_b
        assert loaded.elo0 == test.elo0
        assert loaded.elo1 == test.elo1
        assert loaded.alpha == test.alpha
        assert loaded.beta == test.beta
        assert loaded.status == SPRTStatus.RUNNING
        assert loaded.time_control == test.time_control

    def test_get_nonexistent_returns_none(self, tmp_path: Path) -> None:
        repo = FileSPRTTestRepository(tmp_path)
        assert repo.get_sprt_test("nonexistent") is None

    def test_round_trip_preserves_completed_test(self, tmp_path: Path) -> None:
        repo = FileSPRTTestRepository(tmp_path)
        completed = datetime(2025, 2, 1, 18, 30, 0)
        test = SPRTTest(
            id="test-002",
            engine_a="ea",
            engine_b="eb",
            time_control=FixedTimeControl(movetime_ms=500),
            elo0=0.0,
            elo1=5.0,
            alpha=0.05,
            beta=0.05,
            created_at=datetime(2025, 1, 15, 12, 0, 0),
            status=SPRTStatus.COMPLETED,
            wins=55,
            losses=45,
            draws=100,
            llr=2.97,
            result=SPRTOutcome.H1,
            completed_at=completed,
        )
        repo.save_sprt_test(test)

        loaded = repo.get_sprt_test("test-002")
        assert loaded is not None
        assert loaded.status == SPRTStatus.COMPLETED
        assert loaded.wins == 55
        assert loaded.losses == 45
        assert loaded.draws == 100
        assert loaded.llr == pytest.approx(2.97)  # type: ignore[reportUnknownMemberType]
        assert loaded.result == SPRTOutcome.H1
        assert loaded.completed_at == completed

    def test_creates_meta_json(self, tmp_path: Path) -> None:
        repo = FileSPRTTestRepository(tmp_path)
        repo.save_sprt_test(_make_sprt_test())
        meta_path = tmp_path / "sprt-tests" / "test-001" / "meta.json"
        assert meta_path.is_file()


# ---------------------------------------------------------------------------
# SPRT test filtering
# ---------------------------------------------------------------------------


class TestFileSPRTTestRepositoryFilters:
    """Filter correctness tests for listing SPRT tests."""

    @pytest.fixture()
    def repo_with_tests(self, tmp_path: Path) -> FileSPRTTestRepository:
        repo = FileSPRTTestRepository(tmp_path)
        tests = [
            _make_sprt_test("t1", engine_a="ea", engine_b="eb", status=SPRTStatus.RUNNING),
            _make_sprt_test("t2", engine_a="ea", engine_b="ec", status=SPRTStatus.COMPLETED),
            _make_sprt_test("t3", engine_a="ed", engine_b="ee", status=SPRTStatus.CANCELLED),
        ]
        for t in tests:
            repo.save_sprt_test(t)
        return repo

    def test_list_all(self, repo_with_tests: FileSPRTTestRepository) -> None:
        tests = repo_with_tests.list_sprt_tests()
        assert len(tests) == 3

    def test_filter_by_status(self, repo_with_tests: FileSPRTTestRepository) -> None:
        tests = repo_with_tests.list_sprt_tests(SPRTTestFilter(status=SPRTStatus.RUNNING))
        assert len(tests) == 1
        assert tests[0].id == "t1"

    def test_filter_by_engine_id(self, repo_with_tests: FileSPRTTestRepository) -> None:
        tests = repo_with_tests.list_sprt_tests(SPRTTestFilter(engine_id="ea"))
        assert len(tests) == 2
        ids = {t.id for t in tests}
        assert ids == {"t1", "t2"}

    def test_combined_filter(self, repo_with_tests: FileSPRTTestRepository) -> None:
        tests = repo_with_tests.list_sprt_tests(
            SPRTTestFilter(status=SPRTStatus.COMPLETED, engine_id="ea")
        )
        assert len(tests) == 1
        assert tests[0].id == "t2"

    def test_no_matches(self, repo_with_tests: FileSPRTTestRepository) -> None:
        tests = repo_with_tests.list_sprt_tests(SPRTTestFilter(engine_id="nonexistent"))
        assert tests == []


# ---------------------------------------------------------------------------
# update_sprt_results
# ---------------------------------------------------------------------------


class TestFileSPRTTestRepositoryUpdate:
    """Tests for update_sprt_results."""

    def test_update_results(self, tmp_path: Path) -> None:
        repo = FileSPRTTestRepository(tmp_path)
        test = _make_sprt_test()
        repo.save_sprt_test(test)

        updated = replace(test, wins=10, losses=5, draws=20, llr=1.5)
        repo.update_sprt_results(updated)

        loaded = repo.get_sprt_test("test-001")
        assert loaded is not None
        assert loaded.wins == 10
        assert loaded.losses == 5
        assert loaded.draws == 20
        assert loaded.llr == pytest.approx(1.5)  # type: ignore[reportUnknownMemberType]

    def test_update_nonexistent_raises(self, tmp_path: Path) -> None:
        repo = FileSPRTTestRepository(tmp_path)
        test = _make_sprt_test()
        with pytest.raises(KeyError, match="not found"):
            repo.update_sprt_results(test)


# ---------------------------------------------------------------------------
# Concurrent write safety
# ---------------------------------------------------------------------------


class TestConcurrentWriteSafety:
    """Simulate parallel save_game calls to verify atomic writes."""

    def test_parallel_save_game_no_corruption(self, tmp_path: Path) -> None:
        """Write 20 different games concurrently and verify all are readable."""
        repo = FileGameRepository(tmp_path)
        games = [_make_game(f"game-{i:03d}") for i in range(20)]

        def save(game: Game) -> str:
            repo.save_game(game)
            return game.id

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(save, games))

        assert len(results) == 20

        for game in games:
            loaded = repo.get_game(game.id)
            assert loaded is not None
            assert loaded.id == game.id

    def test_parallel_save_sprt_test_no_corruption(self, tmp_path: Path) -> None:
        """Write 10 different SPRT tests concurrently."""
        repo = FileSPRTTestRepository(tmp_path)
        tests = [_make_sprt_test(f"test-{i:03d}") for i in range(10)]

        def save(test: SPRTTest) -> str:
            repo.save_sprt_test(test)
            return test.id

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(save, tests))

        assert len(results) == 10

        for test in tests:
            loaded = repo.get_sprt_test(test.id)
            assert loaded is not None
            assert loaded.id == test.id


# ---------------------------------------------------------------------------
# FileOpeningBookRepository
# ---------------------------------------------------------------------------


class TestFileOpeningBookRepository:
    """Tests for the opening book file-store implementation."""

    def test_list_books_empty_dir(self, tmp_path: Path) -> None:
        repo = FileOpeningBookRepository(tmp_path)
        assert repo.list_books() == []

    def test_list_books_no_dir(self, tmp_path: Path) -> None:
        repo = FileOpeningBookRepository(tmp_path / "nonexistent")
        assert repo.list_books() == []

    def test_list_books_with_files(self, tmp_path: Path) -> None:
        books_dir = tmp_path / "openings"
        books_dir.mkdir()
        (books_dir / "test.pgn").write_text("1. e4 e5 *")
        (books_dir / "positions.epd").write_text("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR")
        (books_dir / "readme.txt").write_text("not a book")

        repo = FileOpeningBookRepository(tmp_path)
        books = repo.list_books()
        assert len(books) == 2
        names = {b.name for b in books}
        assert "test" in names
        assert "positions" in names

    def test_list_books_sorted(self, tmp_path: Path) -> None:
        books_dir = tmp_path / "openings"
        books_dir.mkdir()
        (books_dir / "b_book.pgn").write_text("data")
        (books_dir / "a_book.epd").write_text("data")

        repo = FileOpeningBookRepository(tmp_path)
        books = repo.list_books()
        assert books[0].name == "a_book"
        assert books[1].name == "b_book"

    def test_save_book(self, tmp_path: Path) -> None:
        repo = FileOpeningBookRepository(tmp_path)
        book = repo.save_book("my_openings.pgn", b"1. e4 e5 *", "pgn")

        assert book.name == "my_openings.pgn"
        assert book.format == "pgn"
        assert book.id  # non-empty UUID
        assert (tmp_path / "openings" / f"{book.id}.pgn").is_file()

    def test_save_book_creates_directory(self, tmp_path: Path) -> None:
        repo = FileOpeningBookRepository(tmp_path)
        assert not (tmp_path / "openings").exists()
        repo.save_book("test.epd", b"data", "epd")
        assert (tmp_path / "openings").is_dir()

    def test_save_book_content_preserved(self, tmp_path: Path) -> None:
        repo = FileOpeningBookRepository(tmp_path)
        content = b"1. e4 e5 2. Nf3 Nc6 *"
        book = repo.save_book("games.pgn", content, "pgn")
        saved = (tmp_path / "openings" / f"{book.id}.pgn").read_bytes()
        assert saved == content

    def test_get_book_path_found(self, tmp_path: Path) -> None:
        repo = FileOpeningBookRepository(tmp_path)
        book = repo.save_book("test.pgn", b"data", "pgn")
        path = repo.get_book_path(book.id)
        assert path is not None
        assert path.is_file()

    def test_get_book_path_not_found(self, tmp_path: Path) -> None:
        repo = FileOpeningBookRepository(tmp_path)
        assert repo.get_book_path("nonexistent") is None

    def test_get_book_path_no_dir(self, tmp_path: Path) -> None:
        repo = FileOpeningBookRepository(tmp_path / "nonexistent")
        assert repo.get_book_path("anything") is None

    def test_round_trip_save_and_list(self, tmp_path: Path) -> None:
        repo = FileOpeningBookRepository(tmp_path)
        repo.save_book("openings.pgn", b"1. d4 d5 *", "pgn")
        repo.save_book("positions.epd", b"fen_data", "epd")

        books = repo.list_books()
        assert len(books) == 2
        formats = {b.format for b in books}
        assert formats == {"pgn", "epd"}
