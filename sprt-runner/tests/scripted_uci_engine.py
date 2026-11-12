"""Scripted UCI engine for tests that need a real subprocess and real time.

Launched with ``sys.executable`` (the runner venv has python-chess), so the
tests need no built engine. Every search sleeps for a scripted duration and
then plays a deterministic legal move, and every ``go`` line received can be
logged to a file so a test can assert on what actually crossed the pipe.

A command arriving mid-search, or the end of input, cuts the sleep short and
the engine goes back to reading commands without answering. So ``quit`` ends
even a search scripted never to finish, as it would on a real engine, instead
of leaving ``UCIClient.quit`` to wait out its 5 s kill timeout.

The engine can also fail to start: before reading anything it can sleep, as a
slow import would, and then it can exit. That sleep isn't cut short by input,
since an engine still importing can't read its commands.

It can also hang. The first engine to create the ``--hang-first`` file writes
its pid there and skips the startup flags. At its first ``go`` it starts to
ignore SIGTERM, adds a line saying ``hung``, and sleeps for an hour without
reading its input, as a search stuck in a loop would. So neither ``quit``, nor
the end of its input, nor SIGTERM ends it: only SIGKILL does. Every later
engine first waits, for up to 20 s, until the first has hung, and then
carries on as its other flags say. So a run can't end before the hang.

Quiet pawn moves are preferred because they cannot repeat a position, which
keeps a game alive long enough for a scripted time loss to be the thing that
ends it.
"""

from __future__ import annotations

import argparse
import os
import select
import signal
import sys
import time
from pathlib import Path

import chess


def _parse_args() -> argparse.Namespace:
    """Parse the engine's scripted behaviour from the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sleep-ms", type=int, default=0, help="Sleep per search")
    parser.add_argument(
        "--first-sleep-ms", type=int, default=None, help="Sleep for the first search only"
    )
    parser.add_argument("--go-log", type=Path, default=None, help="Append each go line to a file")
    parser.add_argument(
        "--exit-at-start", type=int, default=None, help="Exit with this code before reading input"
    )
    parser.add_argument(
        "--startup-delay-ms", type=int, default=0, help="Sleep before reading input"
    )
    parser.add_argument(
        "--hang-first",
        type=Path,
        default=None,
        help="The first engine to create this file hangs at its first go, reading nothing more",
    )
    return parser.parse_args()


def _claim(path: Path) -> bool:
    """Create ``path`` holding this process's pid, unless another process already did."""
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as claimed:
        claimed.write(f"{os.getpid()}\n")
    return True


def _wait_until_hung(path: Path, limit_s: float = 20.0) -> None:
    """Wait until the engine that claimed ``path`` has hung, or ``limit_s`` has passed.

    20 s keeps the wait inside the runner's 30 s handshake limit.
    """
    deadline = time.monotonic() + limit_s
    while time.monotonic() < deadline:
        if "hung" in path.read_text(encoding="utf-8").split():
            return
        time.sleep(0.01)


def _board_from_position(command: str) -> chess.Board:
    """Build the board described by a UCI ``position`` command."""
    head, _, moves = command.partition(" moves ")
    spec = head.removeprefix("position ").strip()
    board = chess.Board() if spec == "startpos" else chess.Board(spec.removeprefix("fen "))
    for uci in moves.split():
        board.push_uci(uci)
    return board


def _choose_move(board: chess.Board) -> str:
    """Pick the first quiet pawn move in UCI order, else the first legal move."""
    moves = sorted(board.legal_moves, key=lambda move: move.uci())
    for move in moves:
        if board.piece_type_at(move.from_square) == chess.PAWN and not board.is_capture(move):
            return move.uci()
    return moves[0].uci()


def _read_command() -> str | None:
    """Read one command from stdin, or return None at the end of input.

    Reads a byte at a time because iterating ``sys.stdin`` reads ahead: a
    ``quit`` arriving in the same chunk as ``go`` would wait in its buffer,
    where the ``select`` in ``_search_is_interrupted`` cannot see it.
    """
    line = bytearray()
    while True:
        byte = os.read(sys.stdin.fileno(), 1)
        if not byte:
            return None
        if byte == b"\n":
            return line.decode().strip()
        line += byte


def _search_is_interrupted(sleep_ms: int) -> bool:
    """Spend the scripted search time, returning True if input cut it short."""
    readable, _, _ = select.select([sys.stdin], [], [], sleep_ms / 1000)
    return bool(readable)


def _send(line: str) -> None:
    """Write one UCI line to stdout."""
    sys.stdout.write(f"{line}\n")
    sys.stdout.flush()


def main() -> None:
    """Run the UCI loop until ``quit`` or end of input."""
    args = _parse_args()
    hang_first: Path | None = args.hang_first
    hangs = hang_first is not None and _claim(hang_first)
    if hang_first is not None and not hangs:
        _wait_until_hung(hang_first)
    if not hangs:
        time.sleep(args.startup_delay_ms / 1000)
        if args.exit_at_start is not None:
            sys.exit(args.exit_at_start)
    go_log: Path | None = args.go_log
    board = chess.Board()
    searches = 0

    for command in iter(_read_command, None):
        if command == "uci":
            _send("id name scripted")
            _send("uciok")
        elif command == "isready":
            _send("readyok")
        elif command.startswith("position "):
            board = _board_from_position(command)
        elif command.startswith("go"):
            if go_log is not None:
                with go_log.open("a", encoding="utf-8") as log:
                    log.write(f"{command}\n")
            if hang_first is not None and hangs:
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
                with hang_first.open("a", encoding="utf-8") as claimed:
                    claimed.write("hung\n")
                time.sleep(3600)
                return
            sleep_ms: int = args.sleep_ms
            if searches == 0 and args.first_sleep_ms is not None:
                sleep_ms = args.first_sleep_ms
            searches += 1
            if _search_is_interrupted(sleep_ms):
                continue
            move = _choose_move(board)
            _send(f"info depth 1 score cp 0 pv {move}")
            _send(f"bestmove {move}")
        elif command == "quit":
            return


if __name__ == "__main__":
    main()
