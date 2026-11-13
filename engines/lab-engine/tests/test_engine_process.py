"""Tests that run the engine, or its build step, as real processes.

They start ``python -m lab_engine`` the way the SPRT runner does, from a
directory that isn't the engine's, so they're integration tests. Each test
signals only the processes it started.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import IO, Any

import pytest
from lab_engine.position import START_FEN, Position, move_to_uci
from lab_engine.runtime import NUMBA_CACHE_ROOT, THREAD_LIMIT_VARIABLES

pytestmark = pytest.mark.integration

ENGINE_DIR = Path(__file__).resolve().parents[1]

WAIT_SECONDS = 30.0
"""The longest a test waits for the engine; it's the SPRT handshake limit."""


class _EngineProcess:
    """``python -m lab_engine`` in a subprocess, read line by line."""

    def __init__(self, cwd: Path) -> None:
        self.stderr_path = cwd / "engine-stderr.log"
        self._stderr = self.stderr_path.open("w")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "lab_engine"],
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
            text=True,
            bufsize=1,
        )
        self.started = time.perf_counter()
        self._lines: queue.Queue[str | None] = queue.Queue()
        assert self.process.stdout is not None
        threading.Thread(target=self._read, args=(self.process.stdout,), daemon=True).start()

    def _read(self, stdout: IO[str]) -> None:
        for line in stdout:
            self._lines.put(line.rstrip("\n"))
        self._lines.put(None)

    def send(self, line: str) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()

    def close_stdin(self) -> None:
        assert self.process.stdin is not None
        self.process.stdin.close()

    def expect(self, prefix: str, timeout: float = WAIT_SECONDS) -> str:
        """Return the next line starting with ``prefix``, skipping others."""
        return self.read_until(prefix, timeout)[-1]

    def read_until(self, prefix: str, timeout: float = WAIT_SECONDS) -> list[str]:
        """Return every line up to and including the next one starting with ``prefix``."""
        deadline = time.monotonic() + timeout
        lines: list[str] = []
        while True:
            remaining = deadline - time.monotonic()
            assert remaining > 0, f"no {prefix!r} within {timeout} s; {self.stderr()}"
            try:
                line = self._lines.get(timeout=remaining)
            except queue.Empty:
                continue
            assert line is not None, f"engine exited before {prefix!r}; {self.stderr()}"
            lines.append(line)
            if line.startswith(prefix):
                return lines

    def wait_for_exit(self, timeout: float = WAIT_SECONDS) -> int:
        return self.process.wait(timeout=timeout)

    def stderr(self) -> str:
        self._stderr.flush()
        return f"stderr: {self.stderr_path.read_text()!r}"

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.kill()  # our own child, not yet reaped: no other process can have its pid
            self.process.wait()
        for stream in (self.process.stdin, self.process.stdout):
            if stream is not None:
                stream.close()
        self._stderr.close()


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[_EngineProcess]:
    process = _EngineProcess(tmp_path)
    yield process
    process.close()


def _legal(fen: str, moves: str = "") -> set[str]:
    position = Position.from_fen(fen)
    for move in moves.split():
        position.push_uci(move)
    return {move_to_uci(m) for m in position.legal_moves()}


class TestTheEngineProcess:
    """The engine speaks UCI over pipes, from any working directory."""

    def test_handshake_then_a_legal_move(self, engine: _EngineProcess) -> None:
        engine.send("uci")
        assert engine.expect("id name") == "id name LabEngine"
        engine.expect("uciok")
        engine.send("isready")
        engine.expect("readyok")
        engine.send("position startpos moves e2e4 c7c5")
        engine.send("go movetime 200")
        bestmove = engine.expect("bestmove")
        assert bestmove.split()[1] in _legal(START_FEN, "e2e4 c7c5")
        engine.send("quit")
        assert engine.wait_for_exit() == 0

    def test_stop_ends_an_infinite_search_promptly(self, engine: _EngineProcess) -> None:
        engine.send("isready")
        engine.expect("readyok")
        engine.send("go infinite")
        # Depth 8 from the start takes several seconds, so this stop lands inside
        # one long compiled call, which must not hold the GIL.
        engine.expect("info depth 7")
        stopped = time.perf_counter()
        engine.send("stop")
        bestmove = engine.expect("bestmove")
        assert time.perf_counter() - stopped < 1.0
        assert bestmove.split()[1] in _legal(START_FEN)
        engine.send("quit")
        assert engine.wait_for_exit() == 0

    def test_warnings_go_to_stderr_not_stdout(self, engine: _EngineProcess) -> None:
        engine.send("position fen not a fen")
        engine.send("isready")
        assert engine.read_until("readyok") == ["readyok"]
        engine.send("quit")
        assert engine.wait_for_exit() == 0
        assert "Ignoring 'position fen not a fen'" in engine.stderr()

    def test_isready_is_answered_during_a_long_depth(self, engine: _EngineProcess) -> None:
        engine.send("isready")
        engine.expect("readyok")
        engine.send("go infinite")
        engine.expect("info depth 7")
        asked = time.perf_counter()
        engine.send("isready")
        engine.expect("readyok")
        assert time.perf_counter() - asked < 1.0
        engine.send("quit")
        assert engine.wait_for_exit() == 0

    def test_quit_during_a_search_exits_promptly(self, engine: _EngineProcess) -> None:
        engine.send("isready")
        engine.expect("readyok")
        engine.send("go infinite")
        engine.expect("info depth 3")
        quit_at = time.perf_counter()
        engine.send("quit")
        assert engine.wait_for_exit() == 0
        assert time.perf_counter() - quit_at < 2.0

    def test_the_end_of_input_ends_the_engine(self, engine: _EngineProcess) -> None:
        engine.send("go infinite")
        engine.close_stdin()
        engine.expect("bestmove")
        assert engine.wait_for_exit() == 0


THREAD_PROBE = """
import importlib.abc, json, os, sys

VARIABLES = {variables!r}
seen = {{}}


class Watch(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name in ("numpy", "numba") and name not in seen:
            seen[name] = {{v: os.environ.get(v) for v in VARIABLES}}
        return None


sys.meta_path.insert(0, Watch())
import lab_engine.uci
import numba

print(json.dumps({{"seen": seen, "numba_threads": numba.get_num_threads()}}))
"""


class TestThreadLimits:
    """numpy and numba load with every thread pool limited to one thread."""

    def test_the_limits_are_set_before_numpy_and_numba_load(self, tmp_path: Path) -> None:
        env = {**os.environ, **dict.fromkeys(THREAD_LIMIT_VARIABLES, "8")}
        probe = THREAD_PROBE.format(variables=THREAD_LIMIT_VARIABLES)
        result = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=WAIT_SECONDS * 4,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        one_each = dict.fromkeys(THREAD_LIMIT_VARIABLES, "1")
        assert report["seen"] == {"numpy": one_each, "numba": one_each}
        assert report["numba_threads"] == 1


CACHE_PROBE = """
import json

import numba  # first, before the package can point it at its cache

import lab_engine.uci
from lab_engine import runtime
from tests.compiled_functions import compiled_functions

paths = [f.stats.cache_path for f in compiled_functions().values()]
print(json.dumps({"cache_dir": str(runtime.numba_cache_dir()), "paths": paths}))
"""

CACHE_PROBE_PACKAGE_FIRST = """
import json

import lab_engine.uci
from lab_engine import runtime
from tests.compiled_functions import compiled_functions

paths = [f.stats.cache_path for f in compiled_functions().values()]
print(json.dumps({"cache_dir": str(runtime.numba_cache_dir()), "paths": paths}))
"""

LOAD_PROBE = """
import json

import lab_engine  # points numba at the cache, as `python -m lab_engine` does first
from numba.core import event

with event.install_recorder("numba:compile") as recorder:
    import lab_engine.uci
from tests.compiled_functions import compiled_functions

functions = compiled_functions().values()
print(json.dumps({
    "events": len(recorder.buffer),
    "functions": len(functions),
    "hits": sum(sum(f.stats.cache_hits.values()) for f in functions),
    "misses": sum(sum(f.stats.cache_misses.values()) for f in functions),
}))
"""


WARM_UP_PROBE = """
import json, os, time

import lab_engine.uci as uci

session = uci.UciSession(lambda line: None)
if os.environ["LAB_ENGINE_WARM_UP"] == "1":
    session.warm_up()
first = time.perf_counter()
session.handle("go depth 1")
session.wait()
print(json.dumps({"first_go_ms": (time.perf_counter() - first) * 1000}))
"""

SOURCE_CHANGE_PROBE = """
import importlib.abc, json, os, sys
from pathlib import Path

import lab_engine
from lab_engine import runtime

# Never edit anything but the copy the test made.
assert runtime.PACKAGE_DIR.is_relative_to(Path(os.environ["LAB_ENGINE_COPY"])), runtime.PACKAGE_DIR
digest = lab_engine.SOURCE_DIGEST
evaluate = runtime.PACKAGE_DIR / "evaluate.py"


class EditDuringImport(importlib.abc.MetaPathFinder):
    # Edit a compiled module's source once the engine has started importing
    # its compiled modules, so that only a check made after them all sees it.
    def find_spec(self, name, path=None, target=None):
        if name == "lab_engine.movegen" and "# edited" not in evaluate.read_text():
            evaluate.write_text(evaluate.read_text() + "\\n# edited\\n")
        return None


sys.meta_path.insert(0, EditDuringImport())
try:
    import lab_engine.uci
    outcome = "started"
except RuntimeError as error:
    outcome = str(error)
print(json.dumps({
    "outcome": outcome,
    "old_cache_left": (runtime.NUMBA_CACHE_ROOT / digest).exists(),
}))
"""


def _run_probe(probe: str, environ: dict[str, str] | None = None) -> dict[str, Any]:
    """Run ``probe`` in a fresh interpreter in the engine directory; return its JSON.

    ``NUMBA_CACHE_DIR`` is removed from the environment unless ``environ`` sets it.
    """
    env = {k: v for k, v in os.environ.items() if k != "NUMBA_CACHE_DIR"}
    env.update(environ or {})
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ENGINE_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=WAIT_SECONDS * 4,
    )
    assert result.returncode == 0, result.stderr
    report: dict[str, Any] = json.loads(result.stdout)
    return report


class TestTheNumbaCache:
    """The build compiles everything into a cache the engine then only loads."""

    def test_the_cache_is_the_sources_even_if_numba_loaded_first(self) -> None:
        report = _run_probe(CACHE_PROBE)
        cache_dir = Path(report["cache_dir"])
        paths: list[str] = report["paths"]
        assert paths
        assert all(Path(path).is_relative_to(cache_dir) for path in paths)

    @pytest.mark.parametrize("probe", [CACHE_PROBE, CACHE_PROBE_PACKAGE_FIRST])
    def test_an_inherited_numba_cache_dir_is_overridden(self, probe: str, tmp_path: Path) -> None:
        elsewhere = tmp_path / "elsewhere"
        report = _run_probe(probe, {"NUMBA_CACHE_DIR": str(elsewhere)})
        paths: list[str] = report["paths"]
        assert paths
        assert all(Path(path).is_relative_to(Path(report["cache_dir"])) for path in paths)
        assert not elsewhere.exists()

    def test_the_warm_up_takes_the_first_gos_one_time_work(self) -> None:
        # numba's first call with the search's arrays does one-time work in
        # Python: about 17 ms on an M4 Max, against 0.1 ms after the warm-up.
        # Load once first, so the measured starts find the cache warm:
        # compiling from a cold one types the modules' arrays, which does the
        # same work and would hide a warm-up that did nothing. The quickest of
        # three warmed-up starts is compared, so one scheduling stall can't
        # fail the test.
        _run_probe('import lab_engine.uci\nprint("{}")')
        warmed = min(
            _run_probe(WARM_UP_PROBE, {"LAB_ENGINE_WARM_UP": "1"})["first_go_ms"] for _ in range(3)
        )
        cold = _run_probe(WARM_UP_PROBE, {"LAB_ENGINE_WARM_UP": "0"})["first_go_ms"]
        assert warmed * 3 < cold

    def test_a_source_change_while_compiling_stops_the_engine(self, tmp_path: Path) -> None:
        # On a copy of the package, so the change can't touch the real source.
        copy = tmp_path / "engine"
        shutil.copytree(
            ENGINE_DIR / "src", copy / "src", ignore=shutil.ignore_patterns("__pycache__")
        )
        report = _run_probe(
            SOURCE_CHANGE_PROBE,
            {
                "PYTHONPATH": f"{copy / 'src'}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
                "LAB_ENGINE_COPY": str(copy),
            },
        )
        assert "changed" in report["outcome"]
        assert report["old_cache_left"] is False

    def test_after_the_build_step_the_engine_compiles_nothing(self, tmp_path: Path) -> None:
        stale = NUMBA_CACHE_ROOT / "0000000000000000" / "stale.nbi"
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_text("from an older source")
        result = subprocess.run(
            [sys.executable, "-m", "lab_engine.warm"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=600,
        )
        assert result.returncode == 0, result.stderr
        assert not stale.exists()

        report = _run_probe(LOAD_PROBE)
        assert report["events"] == 0
        assert report["misses"] == 0
        assert report["hits"] == report["functions"]
        assert report["functions"] > 0

    def test_after_the_build_step_readyok_comes_within_the_play_pages_limit(
        self, tmp_path: Path
    ) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "lab_engine.warm"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=600,
        )
        assert result.returncode == 0, result.stderr
        engine = _EngineProcess(tmp_path)
        try:
            engine.send("uci")
            engine.send("isready")
            engine.expect("readyok", timeout=10.0)
            engine.send("quit")
            assert engine.wait_for_exit() == 0
        finally:
            engine.close()
