"""Tests for the main application factory."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from backend.main import (
    _json_safe,  # type: ignore[reportPrivateUsage]
    create_app,
)
from fastapi.testclient import TestClient


class TestJsonSafe:
    """Tests for `_json_safe`, the non-finite-float guard for 422 bodies."""

    def test_leaves_finite_values_unchanged(self) -> None:
        assert _json_safe({"a": 1, "b": [1.5, "x", None, True]}) == {
            "a": 1,
            "b": [1.5, "x", None, True],
        }

    def test_stringifies_non_finite_floats_anywhere_in_the_tree(self) -> None:
        value: dict[str, Any] = {
            "nan": float("nan"),
            "inf": float("inf"),
            "neg_inf": float("-inf"),
            "nested": {"list": [float("nan"), 1.0, {"deep": float("inf")}]},
        }
        result = _json_safe(value)
        assert result["nan"] == "nan"
        assert result["inf"] == "inf"
        assert result["neg_inf"] == "-inf"
        assert result["nested"]["list"][0] == "nan"
        assert result["nested"]["list"][1] == 1.0
        assert result["nested"]["list"][2]["deep"] == "inf"
        # Non-finite floats are gone; everything else is untouched.
        assert not any(
            isinstance(v, float) and not math.isfinite(v)
            for v in [result["nan"], result["inf"], result["neg_inf"]]
        )


class TestCreateApp:
    """Tests for the application factory."""

    def test_creates_app(self, tmp_path: Path) -> None:
        app = create_app(data_dir=tmp_path)
        assert app.title == "chess-vibe"

    def test_cors_configured(self, tmp_path: Path) -> None:
        app = create_app(data_dir=tmp_path, cors_origins=["http://localhost:3000"])
        # Just verify the app was created successfully
        assert app is not None

    def test_routes_registered(self, tmp_path: Path) -> None:
        app = create_app(data_dir=tmp_path)
        routes = [r.path for r in app.routes if hasattr(r, "path")]  # type: ignore[reportUnknownMemberType,union-attr]
        assert "/api/engines" in routes
        assert "/api/games" in routes
        assert "/api/games/{game_id}" in routes
        assert "/api/sprt/tests" in routes
        assert "/api/sprt/tests/{test_id}" in routes
        assert "/api/openings/books" in routes
        assert "/ws/play" in routes
        assert "/ws/sprt/{test_id}" in routes

    def test_health_check_via_games(self, tmp_path: Path) -> None:
        client = TestClient(create_app(data_dir=tmp_path), raise_server_exceptions=False)
        resp = client.get("/api/games")
        assert resp.status_code == 200

    def test_validation_error_on_unrelated_route_keeps_default_shape(self, tmp_path: Path) -> None:
        """The app-wide `_validation_exception_handler` (added for SPRT's
        NaN/Infinity guard) must not change FastAPI's default 422 body
        shape for routes that have nothing to do with SPRT.
        """
        client = TestClient(create_app(data_dir=tmp_path), raise_server_exceptions=False)
        # POST /api/openings/books requires a `file` field; omitting it
        # triggers FastAPI's own request validation, independent of any
        # SPRT-specific model.
        resp = client.post("/api/openings/books")
        assert resp.status_code == 422
        body = resp.json()
        errors = body["detail"]
        assert errors
        for error in errors:
            assert set(error.keys()) >= {"type", "loc", "msg", "input"}
