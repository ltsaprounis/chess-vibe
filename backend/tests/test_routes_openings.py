"""Tests for the openings routes."""

from __future__ import annotations

from pathlib import Path

import pytest
from backend.main import create_app
from backend.routes import openings
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile


class TestOpeningsRoutes:
    """Tests for GET /openings/books and POST /openings/books."""

    @pytest.fixture
    def data_dir(self, tmp_path: Path) -> Path:
        return tmp_path

    @pytest.fixture
    def client(self, data_dir: Path) -> TestClient:
        return TestClient(create_app(data_dir=data_dir), raise_server_exceptions=False)

    def test_list_books_empty(self, client: TestClient) -> None:
        resp = client.get("/api/openings/books")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_books_with_files(self, client: TestClient, data_dir: Path) -> None:
        books_dir = data_dir / "openings"
        books_dir.mkdir(parents=True)
        (books_dir / "test.pgn").write_text("1. e4 e5 *")
        (books_dir / "positions.epd").write_text("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR")

        resp = client.get("/api/openings/books")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        names = {b["name"] for b in data}
        assert "test" in names
        assert "positions" in names

    def test_list_books_does_not_expose_path(self, client: TestClient, data_dir: Path) -> None:
        """Verify the response does not leak filesystem paths."""
        books_dir = data_dir / "openings"
        books_dir.mkdir(parents=True)
        (books_dir / "test.pgn").write_text("1. e4 e5 *")

        resp = client.get("/api/openings/books")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert "path" not in data[0]

    def test_upload_book_pgn(self, client: TestClient, data_dir: Path) -> None:
        resp = client.post(
            "/api/openings/books",
            files={"file": ("test.pgn", b"1. e4 e5 *", "application/octet-stream")},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "test.pgn"
        assert data["format"] == "pgn"
        assert "path" not in data

        # Verify file was saved via repository (in openings dir)
        books_dir = data_dir / "openings"
        assert books_dir.is_dir()
        saved_files = list(books_dir.glob("*.pgn"))
        assert len(saved_files) == 1

    def test_upload_book_epd(self, client: TestClient) -> None:
        resp = client.post(
            "/api/openings/books",
            files={
                "file": (
                    "positions.epd",
                    b"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR",
                    "application/octet-stream",
                )
            },
        )
        assert resp.status_code == 201
        assert resp.json()["format"] == "epd"

    def test_upload_invalid_format(self, client: TestClient) -> None:
        resp = client.post(
            "/api/openings/books",
            files={"file": ("test.txt", b"hello", "text/plain")},
        )
        assert resp.status_code == 400

    def test_upload_preserves_display_name_in_listing(self, client: TestClient) -> None:
        """The uploaded filename survives the round trip, not just the 201.

        Books are stored under a generated UUID, so this is the assertion
        that the dropdown actually depends on.
        """
        client.post(
            "/api/openings/books",
            files={"file": ("ruy_lopez.pgn", b"1. e4 e5 2. Nf3 Nc6 3. Bb5 *", "text/plain")},
        )

        resp = client.get("/api/openings/books")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "ruy_lopez.pgn"
        assert data[0]["id"] != data[0]["name"]
        assert "path" not in data[0]

    def test_upload_rejects_oversized_file(
        self, client: TestClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The cap is lowered rather than posting tens of megabytes per test."""
        monkeypatch.setattr(openings, "MAX_BOOK_UPLOAD_BYTES", 8)

        resp = client.post(
            "/api/openings/books",
            files={"file": ("big.pgn", b"x" * 64, "text/plain")},
        )
        assert resp.status_code == 413

        books_dir = data_dir / "openings"
        assert list(books_dir.glob("*.pgn")) == []

    def test_upload_at_the_cap_is_accepted(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exactly at the limit is allowed — the check is ``>``, not ``>=``."""
        monkeypatch.setattr(openings, "MAX_BOOK_UPLOAD_BYTES", 8)

        resp = client.post(
            "/api/openings/books",
            files={"file": ("exact.pgn", b"x" * 8, "text/plain")},
        )
        assert resp.status_code == 201

    def test_oversized_rejection_leaks_no_path(
        self, client: TestClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Per the #169/#171 invariant, no filesystem path in the detail."""
        monkeypatch.setattr(openings, "MAX_BOOK_UPLOAD_BYTES", 8)

        resp = client.post(
            "/api/openings/books",
            files={"file": ("big.pgn", b"x" * 64, "text/plain")},
        )
        assert resp.status_code == 413
        assert str(data_dir) not in resp.text
        assert "openings" not in resp.json()["detail"]

    def test_multi_chunk_upload_is_reassembled_intact(
        self, client: TestClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Force the read loop to iterate, then check the bytes survive.

        Every other upload test posts a payload smaller than one chunk, so
        the loop runs once and neither the chunking nor the reassembly is
        exercised at all.
        """
        monkeypatch.setattr(openings, "UPLOAD_CHUNK_BYTES", 8)
        content = bytes(range(256)) * 4  # 1024 bytes = 128 chunks, all distinct

        resp = client.post(
            "/api/openings/books",
            files={"file": ("chunked.pgn", content, "text/plain")},
        )
        assert resp.status_code == 201

        saved = list((data_dir / "openings").glob("*.pgn"))
        assert len(saved) == 1
        assert saved[0].read_bytes() == content

    def test_oversized_upload_stops_reading_at_the_cap(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The excess is never accumulated — reading stops once the cap is crossed.

        Counts reads rather than inspecting memory: with a 4-byte chunk and a
        16-byte cap, a bounded read stops after 5 chunks, while one that
        buffers everything first would make 16.
        """
        monkeypatch.setattr(openings, "UPLOAD_CHUNK_BYTES", 4)
        monkeypatch.setattr(openings, "MAX_BOOK_UPLOAD_BYTES", 16)

        reads = 0
        # The multipart parser instantiates Starlette's UploadFile, not
        # FastAPI's subclass, so the base class is what has to be patched.
        real_read = UploadFile.read

        async def counting_read(self: UploadFile, size: int = -1) -> bytes:
            nonlocal reads
            reads += 1
            return await real_read(self, size)

        monkeypatch.setattr(UploadFile, "read", counting_read)

        resp = client.post(
            "/api/openings/books",
            files={"file": ("big.pgn", b"x" * 64, "text/plain")},
        )
        assert resp.status_code == 413
        assert reads == 5

    def test_upload_truncates_an_overlong_display_name(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The filename is client-controlled and now persisted, so bound it."""
        monkeypatch.setattr(openings, "MAX_BOOK_NAME_CHARS", 12)

        resp = client.post(
            "/api/openings/books",
            files={"file": (f"{'a' * 200}.pgn", b"1. e4 e5 *", "text/plain")},
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "a" * 12

        listed = client.get("/api/openings/books").json()
        assert listed[0]["name"] == "a" * 12

    def test_upload_constants_are_coherent(self) -> None:
        """Guard the constants themselves — every other test overrides them.

        A mistyped cap would reject every real upload. A chunk at least as
        large as the cap would pull the whole payload in on the first read,
        so the limit would be checked only after the memory was already
        committed, which is the thing the chunking exists to prevent.
        """
        assert openings.MAX_BOOK_UPLOAD_BYTES >= 8 * 1024 * 1024
        assert openings.UPLOAD_CHUNK_BYTES < openings.MAX_BOOK_UPLOAD_BYTES
        assert openings.MAX_BOOK_NAME_CHARS >= 64
