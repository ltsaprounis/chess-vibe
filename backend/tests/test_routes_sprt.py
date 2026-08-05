"""Tests for the SPRT routes."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from backend.main import create_app
from fastapi.testclient import TestClient
from shared.storage.file_store import FileSPRTTestRepository
from shared.storage.models import SPRTStatus, SPRTTest
from shared.time_control import FixedTimeControl


class TestSPRTRoutes:
    """Tests for SPRT test endpoints."""

    @pytest.fixture
    def data_dir(self, tmp_path: Path) -> Path:
        return tmp_path

    @pytest.fixture
    def client(self, data_dir: Path) -> TestClient:
        return TestClient(create_app(data_dir=data_dir), raise_server_exceptions=False)

    @pytest.fixture
    def sprt_repo(self, data_dir: Path) -> FileSPRTTestRepository:
        return FileSPRTTestRepository(data_dir)

    def test_get_sprt_test_found(
        self,
        client: TestClient,
        sprt_repo: FileSPRTTestRepository,
    ) -> None:
        test = SPRTTest(
            id="test-1",
            engine_a="engine-a",
            engine_b="engine-b",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=5.0,
            alpha=0.05,
            beta=0.05,
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            status=SPRTStatus.RUNNING,
            wins=5,
            losses=3,
            draws=2,
            llr=0.5,
        )
        sprt_repo.save_sprt_test(test)

        resp = client.get("/api/sprt/tests/test-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "test-1"
        assert data["status"] == "running"
        assert data["wins"] == 5
        assert data["losses"] == 3

    def test_list_sprt_tests_empty(self, client: TestClient) -> None:
        resp = client.get("/api/sprt/tests")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_sprt_tests_returns_saved_tests(
        self,
        client: TestClient,
        sprt_repo: FileSPRTTestRepository,
    ) -> None:
        test = SPRTTest(
            id="test-list-1",
            engine_a="engine-a",
            engine_b="engine-b",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=5.0,
            alpha=0.05,
            beta=0.05,
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            status=SPRTStatus.RUNNING,
            wins=5,
            losses=3,
            draws=2,
            llr=0.5,
        )
        sprt_repo.save_sprt_test(test)

        resp = client.get("/api/sprt/tests")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "test-list-1"
        assert data[0]["status"] == "running"

    def test_get_sprt_test_not_found(self, client: TestClient) -> None:
        resp = client.get("/api/sprt/tests/nonexistent")
        assert resp.status_code == 404

    def test_cancel_nonexistent_test(self, client: TestClient) -> None:
        resp = client.post("/api/sprt/tests/nonexistent/cancel")
        assert resp.status_code == 404

    def test_create_sprt_test_invalid_book_id_returns_400(self, client: TestClient) -> None:
        """POST /sprt/tests with a non-existent book_id returns 400."""
        resp = client.post(
            "/api/sprt/tests",
            json={
                "engine_a": "engine-a",
                "engine_b": "engine-b",
                "time_control": "movetime=100",
                "book_id": "nonexistent-book",
            },
        )
        assert resp.status_code == 400
        assert "nonexistent-book" in resp.json()["detail"]

    def test_create_sprt_test_valid_book_id_resolves_path(
        self, client: TestClient, data_dir: Path
    ) -> None:
        """POST /sprt/tests with a valid book_id resolves to a filesystem path."""
        books_dir = data_dir / "openings"
        books_dir.mkdir(parents=True)
        book_file = books_dir / "my-book.pgn"
        book_file.write_text("1. e4 e5 *")

        # The book repo uses the stem as the ID
        book_id = "my-book"

        with patch(
            "backend.services.sprt_service.SPRTService.start_test",
            new_callable=AsyncMock,
            return_value="test-123",
        ) as mock_start:
            resp = client.post(
                "/api/sprt/tests",
                json={
                    "engine_a": "engine-a",
                    "engine_b": "engine-b",
                    "time_control": "movetime=100",
                    "book_id": book_id,
                },
            )
            assert resp.status_code == 201
            # Verify the resolved filesystem path was passed to start_test
            mock_start.assert_called_once()
            call_kwargs = mock_start.call_args
            assert call_kwargs.kwargs["book_path"] == str(book_file)

    def test_start_failure_does_not_leak_filesystem_path(self, client: TestClient) -> None:
        """A runner launch failure must not echo the interpreter path (#162).

        ``create_subprocess_exec`` raises ``OSError`` whose message embeds
        the runner interpreter's absolute path; returning ``str(e)`` as the
        error detail would leak it to the frontend.
        """
        leaked_path = "/Users/someone/repos/chess-vibe/sprt-runner/.venv/bin/python"
        with patch(
            "backend.services.sprt_service.SPRTService.start_test",
            new_callable=AsyncMock,
            side_effect=OSError(f"[Errno 2] No such file or directory: '{leaked_path}'"),
        ):
            resp = client.post(
                "/api/sprt/tests",
                json={
                    "engine_a": "engine-a",
                    "engine_b": "engine-b",
                    "time_control": "movetime=100",
                },
            )

        assert resp.status_code == 500
        detail: str = resp.json()["detail"]
        assert detail == "Failed to start the SPRT test"
        assert leaked_path not in detail
        # No filesystem path can be present at all if there is no separator.
        assert "/" not in detail


def _valid_payload(**overrides: Any) -> dict[str, Any]:
    """Build a valid POST /sprt/tests body, overridden field by field."""
    payload: dict[str, Any] = {
        "engine_a": "engine-a",
        "engine_b": "engine-b",
        "time_control": "movetime=1000",
        "elo0": 0.0,
        "elo1": 5.0,
        "alpha": 0.05,
        "beta": 0.05,
        "concurrency": 1,
    }
    payload.update(overrides)
    return payload


def _assert_422_mentions(response: httpx.Response, *expected_substrings: str) -> None:
    """Assert a 422 response body's error details mention every given substring.

    Checks the ``loc`` and ``msg`` of every error entry so both
    field-scoped errors (e.g. ``alpha``) and whole-model errors (e.g.
    the ``elo0 < elo1`` check) can be asserted the same way.
    """
    assert response.status_code == 422, response.text
    body: dict[str, Any] = response.json()
    errors: list[dict[str, Any]] = body["detail"]
    assert errors, body
    haystack = " ".join(
        f"{' '.join(str(p) for p in e.get('loc', []))} {e.get('msg', '')}" for e in errors
    )
    for substring in expected_substrings:
        assert substring in haystack, f"expected {substring!r} in error details: {errors}"


class TestSPRTTestCreateRequestValidation:
    """Validation rules for POST /sprt/tests request bodies (issue #163)."""

    @pytest.fixture
    def data_dir(self, tmp_path: Path) -> Path:
        return tmp_path

    @pytest.fixture
    def client(self, data_dir: Path) -> TestClient:
        return TestClient(create_app(data_dir=data_dir), raise_server_exceptions=False)

    def _post(
        self,
        client: TestClient,
        payload: Mapping[str, Any] | None = None,
        body: bytes | None = None,
    ) -> tuple[httpx.Response, AsyncMock]:
        """POST to /api/sprt/tests with SPRTService.start_test mocked out.

        Either a JSON-serialisable ``payload`` or raw ``body`` bytes
        (for values like NaN that the standard JSON encoder rejects)
        may be supplied. Returns both the HTTP response and the mock,
        so callers can assert what (if anything) was forwarded to the
        service — e.g. that a rejected payload never reaches it, or
        that an accepted one is forwarded correctly normalised.
        """
        with patch(
            "backend.services.sprt_service.SPRTService.start_test",
            new_callable=AsyncMock,
            return_value="test-123",
        ) as mock_start:
            if body is not None:
                response = client.post(
                    "/api/sprt/tests",
                    content=body,
                    headers={"content-type": "application/json"},
                )
            else:
                response = client.post("/api/sprt/tests", json=payload)
            return response, mock_start

    # -- valid request still works -----------------------------------

    def test_valid_request_returns_201(self, client: TestClient) -> None:
        resp, mock_start = self._post(client, _valid_payload())
        assert resp.status_code == 201, resp.text
        assert resp.json() == {"id": "test-123", "status": "running"}
        mock_start.assert_called_once()
        kwargs = mock_start.call_args.kwargs
        assert kwargs["engine_a"] == "engine-a"
        assert kwargs["engine_b"] == "engine-b"
        assert kwargs["time_control_str"] == "movetime=1000"
        assert kwargs["concurrency"] == 1

    # -- elo0 < elo1 ----------------------------------------------------

    def test_elo0_equal_elo1_rejected(self, client: TestClient) -> None:
        resp, mock_start = self._post(client, _valid_payload(elo0=5.0, elo1=5.0))
        _assert_422_mentions(resp, "elo0", "elo1")
        mock_start.assert_not_called()

    def test_elo0_greater_than_elo1_rejected(self, client: TestClient) -> None:
        resp, mock_start = self._post(client, _valid_payload(elo0=10.0, elo1=5.0))
        _assert_422_mentions(resp, "elo0", "elo1")
        mock_start.assert_not_called()

    def test_elo0_less_than_elo1_accepted(self, client: TestClient) -> None:
        resp, mock_start = self._post(client, _valid_payload(elo0=0.0, elo1=5.0))
        assert resp.status_code == 201, resp.text
        mock_start.assert_called_once()

    # -- elo0 / elo1 magnitude bounds ---------------------------------

    @pytest.mark.parametrize(
        "elo0,elo1",
        [(-1000.1, 0.0), (0.0, 1000.1), (-1e308, 5.0)],
    )
    def test_elo_beyond_magnitude_bound_rejected(
        self, client: TestClient, elo0: float, elo1: float
    ) -> None:
        resp, mock_start = self._post(client, _valid_payload(elo0=elo0, elo1=elo1))
        assert resp.status_code == 422, resp.text
        mock_start.assert_not_called()

    def test_elo_at_magnitude_bound_accepted(self, client: TestClient) -> None:
        resp, mock_start = self._post(client, _valid_payload(elo0=-1000.0, elo1=1000.0))
        assert resp.status_code == 201, resp.text
        mock_start.assert_called_once()

    # -- alpha / beta bounds ---------------------------------------------

    @pytest.mark.parametrize("field", ["alpha", "beta"])
    @pytest.mark.parametrize("value", [0.0, 1.0, -0.1, 1.1, -1, 5])
    def test_alpha_beta_out_of_range_rejected(
        self, client: TestClient, field: str, value: float
    ) -> None:
        resp, mock_start = self._post(client, _valid_payload(**{field: value}))
        _assert_422_mentions(resp, field)
        mock_start.assert_not_called()

    @pytest.mark.parametrize("field", ["alpha", "beta"])
    @pytest.mark.parametrize("value", [0.01, 0.05, 0.5, 0.99])
    def test_alpha_beta_in_range_accepted(
        self, client: TestClient, field: str, value: float
    ) -> None:
        resp, mock_start = self._post(client, _valid_payload(**{field: value}))
        assert resp.status_code == 201, resp.text
        mock_start.assert_called_once()

    # -- concurrency bounds ------------------------------------------------

    @pytest.mark.parametrize("value", [0, -1, 65])
    def test_concurrency_out_of_range_rejected(self, client: TestClient, value: int) -> None:
        resp, mock_start = self._post(client, _valid_payload(concurrency=value))
        _assert_422_mentions(resp, "concurrency")
        mock_start.assert_not_called()

    @pytest.mark.parametrize("value", [1, 64])
    def test_concurrency_bounds_accepted(self, client: TestClient, value: int) -> None:
        resp, mock_start = self._post(client, _valid_payload(concurrency=value))
        assert resp.status_code == 201, resp.text
        assert mock_start.call_args.kwargs["concurrency"] == value

    # -- engine_a / engine_b non-empty ------------------------------------

    @pytest.mark.parametrize("field", ["engine_a", "engine_b"])
    @pytest.mark.parametrize("value", ["", "   ", "\t\n", "\xa0"])
    def test_empty_or_whitespace_engine_id_rejected(
        self, client: TestClient, field: str, value: str
    ) -> None:
        resp, mock_start = self._post(client, _valid_payload(**{field: value}))
        _assert_422_mentions(resp, field)
        mock_start.assert_not_called()

    def test_engine_ids_are_stripped_before_forwarding(self, client: TestClient) -> None:
        resp, mock_start = self._post(
            client, _valid_payload(engine_a="  engine-a  ", engine_b="engine-b\t")
        )
        assert resp.status_code == 201, resp.text
        kwargs = mock_start.call_args.kwargs
        assert kwargs["engine_a"] == "engine-a"
        assert kwargs["engine_b"] == "engine-b"

    # -- engine_a / engine_b length bound ----------------------------------

    @pytest.mark.parametrize("field", ["engine_a", "engine_b"])
    def test_engine_id_too_long_rejected(self, client: TestClient, field: str) -> None:
        resp, mock_start = self._post(client, _valid_payload(**{field: "e" * 257}))
        assert resp.status_code == 422, resp.text
        errors = resp.json()["detail"]
        assert any(e["type"] == "string_too_long" for e in errors), errors
        mock_start.assert_not_called()

    def test_engine_id_at_max_length_accepted(self, client: TestClient) -> None:
        resp, mock_start = self._post(client, _valid_payload(engine_a="e" * 256))
        assert resp.status_code == 201, resp.text
        mock_start.assert_called_once()

    # -- engine_a / engine_b non-printable characters ----------------------

    @pytest.mark.parametrize("field", ["engine_a", "engine_b"])
    @pytest.mark.parametrize(
        "value",
        ["eng\x00ine", "eng\nine", "eng\tine", "eng​ine"],
        ids=["nul", "newline", "tab", "zero-width-space"],
    )
    def test_non_printable_char_in_engine_id_rejected(
        self, client: TestClient, field: str, value: str
    ) -> None:
        resp, mock_start = self._post(client, _valid_payload(**{field: value}))
        _assert_422_mentions(resp, field, "non-printable")
        mock_start.assert_not_called()

    # -- time_control ------------------------------------------------------

    @pytest.mark.parametrize("value", ["", "   ", "\t\n"])
    def test_blank_time_control_rejected_with_422(self, client: TestClient, value: str) -> None:
        resp, mock_start = self._post(client, _valid_payload(time_control=value))
        _assert_422_mentions(resp, "time_control")
        mock_start.assert_not_called()

    @pytest.mark.parametrize(
        "value",
        [
            "garbage",
            "movetime=abc",
            "movetime=0",
            "movetime=-5",
            # No '=' at all: shared.time_control.parse_time_control builds a
            # dict via `dict(part.split("=", 1) for part in ...)`, which
            # raises its own internal "dictionary update sequence element
            # #0 has length 1; 2 is required" ValueError for these before
            # ever reaching its "Unknown time control format" branch. The
            # 422 message must not leak that internal text (issue #163
            # review finding 1) — it must describe the supported formats.
            "60+1",
            "10+0.1",
            "1000",
        ],
    )
    def test_unparseable_time_control_rejected_with_descriptive_422(
        self, client: TestClient, value: str
    ) -> None:
        resp, mock_start = self._post(client, _valid_payload(time_control=value))
        _assert_422_mentions(resp, "time_control", "movetime")
        assert "dictionary update sequence" not in resp.text
        mock_start.assert_not_called()

    @pytest.mark.parametrize(
        "value",
        [
            "movetime=1000",
            "depth=10",
            "nodes=50000",
            "wtime=60000,btime=60000,winc=1000,binc=1000",
        ],
    )
    def test_valid_time_control_forms_accepted(self, client: TestClient, value: str) -> None:
        resp, mock_start = self._post(client, _valid_payload(time_control=value))
        assert resp.status_code == 201, resp.text
        mock_start.assert_called_once()

    @pytest.mark.parametrize("value", [" movetime=1000", "movetime=1000 ", " movetime=1000 "])
    def test_time_control_whitespace_is_stripped_before_forwarding(
        self, client: TestClient, value: str
    ) -> None:
        resp, mock_start = self._post(client, _valid_payload(time_control=value))
        assert resp.status_code == 201, resp.text
        assert mock_start.call_args.kwargs["time_control_str"] == "movetime=1000"

    def test_time_control_too_long_rejected(self, client: TestClient) -> None:
        resp, mock_start = self._post(
            client, _valid_payload(time_control="movetime=1000," + "x" * 128)
        )
        assert resp.status_code == 422, resp.text
        errors = resp.json()["detail"]
        assert any(e["type"] == "string_too_long" for e in errors), errors
        mock_start.assert_not_called()

    @pytest.mark.parametrize(
        "value", ["movetime=1000\x00", "movetime=1​000"], ids=["nul", "zero-width-space"]
    )
    def test_non_printable_char_in_time_control_rejected(
        self, client: TestClient, value: str
    ) -> None:
        resp, mock_start = self._post(client, _valid_payload(time_control=value))
        _assert_422_mentions(resp, "time_control", "non-printable")
        mock_start.assert_not_called()

    # -- NaN / Infinity guard on float fields -----------------------------

    @pytest.mark.parametrize("field", ["elo0", "elo1", "alpha", "beta"])
    @pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
    def test_non_finite_float_rejected(self, client: TestClient, field: str, token: str) -> None:
        # json.dumps(..., allow_nan=False) (httpx's default) refuses to emit
        # NaN/Infinity, so the body is built by hand with the bare token —
        # matching how a non-finite literal would actually arrive on the wire.
        body_dict = _valid_payload()
        del body_dict[field]
        rest = json.dumps(body_dict)[1:-1]
        raw = ("{" + rest + (", " if rest else "") + f'"{field}": {token}' + "}").encode()
        resp, mock_start = self._post(client, body=raw)
        _assert_422_mentions(resp, field)
        # Pin the rejection *reason*, not just the field name: dropping
        # `allow_inf_nan=False` would make some of these fail for the
        # wrong reason instead (e.g. via `lt=1`/`gt=0` on alpha/beta, or
        # via the elo0<elo1 model validator) and still pass a field-name-
        # only assertion.
        errors = resp.json()["detail"]
        matching = [e for e in errors if field in " ".join(str(p) for p in e.get("loc", []))]
        assert matching, errors
        assert any(e["type"] == "finite_number" for e in matching), matching
        mock_start.assert_not_called()
