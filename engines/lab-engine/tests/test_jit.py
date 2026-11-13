"""Tests that the engine compiles everything at import, and caches it by source."""

from __future__ import annotations

from pathlib import Path

import pytest
from lab_engine import runtime
from lab_engine.uci import UciSession
from numba.core.event import install_recorder

from tests.compiled_functions import compiled_functions
from tests.test_search import WAIT_SECONDS


class TestCompiledFunctions:
    """Every compiled function is compiled from its signature, then sealed."""

    def test_finds_the_engines_compiled_functions(self) -> None:
        names = set(compiled_functions())
        assert {
            "lab_engine.movegen.generate_moves",
            "lab_engine.movegen.make_move",
            "lab_engine.movegen.perft",
            "lab_engine.evaluate.evaluate",
            "lab_engine.search.alpha_beta",
        } <= names

    def test_each_was_compiled_at_import_and_can_compile_nothing_more(self) -> None:
        for name, function in compiled_functions().items():
            assert function.signatures, f"{name} has no compiled signature"
            with pytest.raises(RuntimeError, match="compilation disabled"):
                function.compile("int64(int64)")

    def test_each_caches_in_the_directory_named_after_the_source(self) -> None:
        cache_dir = runtime.numba_cache_dir()
        for name, function in compiled_functions().items():
            assert Path(function.stats.cache_path).is_relative_to(cache_dir), name


class TestNoCompilingAfterReadyok:
    """Once the engine has answered ``readyok``, a search compiles nothing."""

    def test_the_first_go_after_readyok_compiles_nothing(self) -> None:
        lines: list[str] = []
        session = UciSession(lines.append)
        session.handle("uci")
        session.handle("isready")
        assert lines[-1] == "readyok"
        with install_recorder("numba:compile") as recorder:
            session.handle("position startpos moves e2e4 e7e5")
            session.handle("go depth 4")
            assert session.wait(WAIT_SECONDS)
        assert recorder.buffer == []
        assert lines[-1].startswith("bestmove ")
