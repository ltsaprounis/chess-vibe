"""Tests for lab_engine.warm, the build's last step.

``warm()`` here gets a scratch cache root, so the engine's own cache is left
alone. ``test_engine_process.py``'s ``TestTheNumbaCache`` runs the real build
step in a separate process.
"""

from __future__ import annotations

from pathlib import Path

from lab_engine import warm


class TestWarm:
    """``warm`` starts the cache from nothing, then compiles the engine."""

    def test_deletes_every_cache_under_the_root(self, tmp_path: Path) -> None:
        root = tmp_path / "numba"
        stale = root / "0123456789abcdef" / "lab_engine_x" / "search.alpha_beta-1.py313.nbi"
        stale.parent.mkdir(parents=True)
        stale.write_text("stale")
        (root / "notes").write_text("x")
        warm.warm(root)
        assert not stale.exists()
        assert not (root / "notes").exists()

    def test_a_missing_root_is_fine(self, tmp_path: Path) -> None:
        warm.warm(tmp_path / "missing")

    def test_compiles_what_the_engine_runs(self) -> None:
        main = (Path(warm.__file__).parent / "__main__.py").read_text()
        assert f"from {warm.ENGINE_MODULE} import main" in main
