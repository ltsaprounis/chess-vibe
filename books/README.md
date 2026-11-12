# Opening books

The SPRT runner plays its games from an opening book. Without `--book`, it
plays from `2moves_v1.epd` here, resolved from its working directory, which
is the repo root.

## 2moves_v1.epd

- **Positions:** 40,457, one FEN per line, each after two moves a side, with
  White to move. None appears twice.
- **Source:** `2moves_v1.epd`, unzipped from `2moves_v1.epd.zip` in
  [official-stockfish/books](https://github.com/official-stockfish/books) at
  commit `d2356e2da603f6402955e87bae5f55535f200e16`, and vendored unmodified.
- **Licence:** CC0 1.0 Universal, a public-domain dedication, as that
  repository's
  [LICENSE](https://github.com/official-stockfish/books/blob/d2356e2da603f6402955e87bae5f55535f200e16/LICENSE)
  file says. It was added in
  [official-stockfish/books#58](https://github.com/official-stockfish/books/pull/58).
  Unlike the rest of chess-vibe, the book isn't GPL.
- **Checksums:**
  - SHA-256: `dc91f225bc93e7ec091095bf8264595da33d36b9d3ac97ddd2dd54bc3a094fa4`
  - SHA-384, base64: `3KqrVhD252BCXrqwqVmFeYnLRp+ckuubLFo+9OFLic3iTuzrfV563rNMUB+vovpz`,
    the `sri` that upstream's `books.json` records for it

`sprt-runner/tests/test_default_book.py` checks the SHA-384 and every
position.

## How a run uses its book

- Each run shuffles the book's positions with a seed. Each position gives two
  games in a row, one with each engine as White.
- `--seed N` replays an order. Without it, the runner picks a seed and records
  it on its first output line, `{"type": "start", "seed": N}`.
- A book with no positions stops the run with an error.
- Once a run has used every position, it logs a warning, because positions
  then repeat, and a deterministic engine replays the same game from the same
  position.

## Using another book

Pass `--book PATH` to the runner. An EPD book has one position per line, and a
PGN book one game per opening, whose final position is where play starts. The
web UI can't upload a book: upload it with the backend's
`POST /api/openings/books`, and the SPRT page then offers it.

A book added to this directory should have its source and licence recorded
here.
