"""Tests for the games routes."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from backend.main import create_app
from fastapi.testclient import TestClient
from shared.storage.file_store import FileGameRepository
from shared.storage.models import Game, GameResult, Move


class TestGamesRoutes:
    """Tests for GET /games and GET /games/{id}."""

    @pytest.fixture
    def data_dir(self, tmp_path: Path) -> Path:
        return tmp_path

    @pytest.fixture
    def client(self, data_dir: Path) -> TestClient:
        return TestClient(create_app(data_dir=data_dir), raise_server_exceptions=False)

    @pytest.fixture
    def game_repo(self, data_dir: Path) -> FileGameRepository:
        return FileGameRepository(data_dir)

    @pytest.fixture
    def sample_game(self) -> Game:
        return Game(
            id="game-1",
            white_engine="engine-a",
            black_engine="engine-b",
            result=GameResult.WHITE_WIN,
            moves=[
                Move(
                    uci="e2e4",
                    san="e4",
                    fen_after="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
                ),
            ],
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
        )

    def test_list_games_empty(self, client: TestClient) -> None:
        resp = client.get("/api/games")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_games_with_data(
        self,
        client: TestClient,
        game_repo: FileGameRepository,
        sample_game: Game,
    ) -> None:
        game_repo.save_game(sample_game)
        resp = client.get("/api/games")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "game-1"
        assert data[0]["result"] == "1-0"
        assert data[0]["move_count"] == 1

    def test_list_games_filter_by_engine(
        self,
        client: TestClient,
        game_repo: FileGameRepository,
        sample_game: Game,
    ) -> None:
        game_repo.save_game(sample_game)
        resp = client.get("/api/games", params={"engine_id": "engine-a"})
        assert resp.status_code == 200
        assert len(resp.json()) == 1

        resp = client.get("/api/games", params={"engine_id": "engine-x"})
        assert resp.status_code == 200
        assert len(resp.json()) == 0

    def test_list_games_invalid_result_filter(self, client: TestClient) -> None:
        resp = client.get("/api/games", params={"result": "invalid"})
        assert resp.status_code == 400

    def test_get_game_found(
        self,
        client: TestClient,
        game_repo: FileGameRepository,
        sample_game: Game,
    ) -> None:
        game_repo.save_game(sample_game)
        resp = client.get("/api/games/game-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "game-1"
        assert data["white_engine"] == "engine-a"
        assert len(data["moves"]) == 1

    def test_get_game_not_found(self, client: TestClient) -> None:
        resp = client.get("/api/games/nonexistent")
        assert resp.status_code == 404
