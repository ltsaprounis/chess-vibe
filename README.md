# chess-vibe

A development suite for building and testing chess engines — play games, run SPRT tests, and track progress. 

This project aims to have components that are either 100% AI generated or 100% human generated. The workfolow is heavily inspired by [marcgs/SplitVibe](https://github.com/marcgs/SplitVibe)

## Stack

| Component | Tech |
|---|---|
| Backend | Python 3.13+, FastAPI |
| SPRT Runner | Python 3.13+, asyncio + multiprocessing |
| Shared lib | Python 3.13+, python-chess |
| Frontend | React 19, TypeScript, Vite, Tailwind CSS |

## Running Tests

### Unit Tests

Unit tests run quickly and do not require any engine builds:

```bash
make test          # Run unit tests for all components
```

### Integration Tests

Integration tests require a built `random-engine` venv. To set up:

```bash
make setup         # Builds all components including random-engine
```

Then run integration tests:

```bash
make test-integration   # Run integration tests only
make test-all           # Run both unit and integration tests
```

If the `random-engine` venv is not built, integration tests will skip automatically with a clear message.

### CI Behaviour

- **Pull requests**: CI runs unit tests only (`make test`).
- **Pushes to `main`**: CI runs both unit tests and integration tests.

## UI Screenshots

`docs/screenshots/` holds committed screenshots of the frontend, used as
visual evidence on pull requests that change the UI. Regenerate them with:

```bash
cd frontend && npm run screenshots
```

The script boots Vite in-process on a free port and serves fixture data by
intercepting `/api/**` in the browser, so **no backend, no dev server and no
seeded `data/` are needed**, and the output is deterministic — rerunning it
with no UI change produces byte-identical PNGs.

It drives the browser through `playwright-core`, which uses the Chrome
already installed on your machine instead of downloading its own (set
`CHROME_PATH` to point at a different Chromium-based binary). CI never runs
this script.

## License

Licensed under [GPL-3.0-or-later](LICENSE), because the project builds on
[python-chess](https://github.com/niklasf/python-chess) and
[Stockfish](https://github.com/official-stockfish/Stockfish), both GPL-3.
