"""A scripted UCI engine for the end-to-end tests, copied into a throwaway repository.

Its behaviour comes from a ``mode`` file beside it, so a commit changes how
strong it is by changing a file in its own directory, as a real candidate
would:

- ``fast``: answers every ``go`` at once;
- ``slow``: takes a second over every move, so with a short clock it loses
  on time at its first move;
- ``think:<ms>``: takes that many milliseconds over every move.

It always plays the first legal move in UCI order, so games are repeatable.
It runs on the lab venv's interpreter, which has python-chess through shared.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import chess


def _delay() -> float:
    mode = (Path(__file__).resolve().parent / "mode").read_text().strip()
    if mode == "fast":
        return 0.0
    if mode == "slow":
        return 1.0
    if mode.startswith("think:"):
        return int(mode.removeprefix("think:")) / 1000.0
    raise SystemExit(f"unknown mode {mode!r}")


def _position(words: list[str]) -> chess.Board:
    if words[1] == "startpos":
        board, rest = chess.Board(), words[2:]
    else:
        end = words.index("moves") if "moves" in words else len(words)
        board, rest = chess.Board(" ".join(words[2:end])), words[end:]
    for move in rest[1:] if rest and rest[0] == "moves" else []:
        board.push_uci(move)
    return board


def main() -> None:
    delay = _delay()
    board = chess.Board()

    def say(line: str) -> None:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    for raw in sys.stdin:
        words = raw.split()
        if not words:
            continue
        command = words[0]
        if command == "uci":
            say("id name lab-test-engine")
            say("uciok")
        elif command == "isready":
            say("readyok")
        elif command == "position":
            board = _position(words)
        elif command == "go":
            time.sleep(delay)
            move = min(board.legal_moves, key=lambda m: m.uci())
            say("info depth 1 score cp 0 nodes 1 time 1 pv " + move.uci())
            say(f"bestmove {move.uci()}")
        elif command == "quit":
            return


if __name__ == "__main__":
    main()
