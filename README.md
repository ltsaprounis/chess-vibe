# chess-vibe

A development suite for building and testing chess engines — play games, run SPRT tests, and track progress. 

This project aims to have components that are either 100% AI generated or 100% human generated. The workfolow is heavily inspired by [marcgs/SplitVibe](https://github.com/marcgs/SplitVibe)

## Stack

| Component | Tech |
|---|---|
| Backend | Python 3.14+, FastAPI |
| SPRT Runner | Python 3.14+, asyncio + multiprocessing |
| Shared lib | Python 3.14+, python-chess |
| Frontend | React 18, TypeScript, Vite, Tailwind CSS |

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
