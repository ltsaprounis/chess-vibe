# OpenTelemetry Strategy

Cross-cutting observability layer for the chess engine suite. The engine itself is **never instrumented** — even lightweight span creation in a hot search loop would destroy NPS (nodes per second). All measurement of the engine happens from the outside.

## Instrumentation targets

| Component | Instrument? | Rationale |
|---|---|---|
| **Backend (FastAPI)** | Yes | Primary target — traces every request/WebSocket, spans for engine calls, SPRT launches, storage operations |
| **SPRT Runner** | Yes, carefully | Long-running batch jobs — visibility into test progress, game durations, engine crashes, adjudication rates |
| **Shared (storage)** | Yes | Trace storage read/write latency — important for evaluating FileStore performance and validating future SQLite migration |
| **Frontend** | Optional | Browser-side tracing (page loads, WebSocket latency) — lower priority |
| **Engine** | No | Hot path — never instrument. Measure from the outside via spans around `go()` calls |

## Key principles

1. **Instrument at component boundaries, not inside hot paths.** Wrap callers with spans, not the engine or low-level UCI protocol parsing.
2. **One span per "engine thinks about a move"**, not per UCI line. Don't instrument `shared/uci_client.py` at the protocol level — wrap the callers (`game_manager.py`, `game.py`) instead. Otherwise every `isready` ping and `position` command creates a span (noise).
3. **Engine is measured from the outside.** All engines (internal and external) are driven as UCI subprocesses via `shared/uci_client.py`. The callers (`game_manager.py`, `game.py`) create a span around the UCI `go` → `bestmove` round-trip, measuring time-to-bestmove (I/O wait, not CPU — zero overhead on the engine).

## What to measure

### Traces
- **Backend**: Request latency, WebSocket session duration, engine move latency, storage read/write duration
- **SPRT Runner**: Full test duration, individual game duration, games/second throughput, adjudication frequency

### Metrics
- Games completed per minute (SPRT throughput)
- Engine move latency (P50/P95/P99)
- Active WebSocket connections
- Storage operation latency (read/write, by operation type)
- SPRT LLR progression rate
- Engine crash rate during SPRT

## Cross-process context propagation

### Backend → SPRT Runner (CLI subprocess)
The backend invokes the SPRT runner as a subprocess. To get connected traces (backend span → runner spans), inject the trace context into the subprocess invocation via the `TRACEPARENT` environment variable. The runner extracts it on startup. This is supported by OTEL but must be wired explicitly.

### SPRT Runner → concurrent games
- **`asyncio`**: Works with `opentelemetry-instrumentation-asyncio`. Ensure context propagates across `create_task` boundaries.
- **`multiprocessing`**: OTEL context does **not** propagate across process boundaries by default. Serialize the trace context and pass it explicitly (e.g., via environment variable or argument) to child processes.

## Exporter strategy

Start simple, upgrade when needed:

1. **Phase 1 — Console/JSON file exporter**: Zero infrastructure, grep-able, good for development. Export spans to a JSON-lines file alongside the data directory.
2. **Phase 2 — Local Jaeger or Grafana Tempo**: Run as a Docker container when visual trace exploration is needed. Particularly useful for debugging slow SPRT tests or storage bottlenecks.
3. The OTEL SDK abstracts the exporter — swapping from file to Jaeger requires only configuration changes, no code changes.

### Local visualization options

**Jaeger all-in-one** (recommended for Phase 2) — single Docker command, no config:

```bash
docker run -d --name jaeger \
  -p 16686:16686 \
  -p 4317:4317 \
  -p 4318:4318 \
  jaegertracing/all-in-one:latest
```

- `localhost:16686` — Web UI (searchable service/operation/duration view)
- `localhost:4317` — OTLP gRPC receiver (configure OTEL SDK to export here)
- `localhost:4318` — OTLP HTTP receiver (alternative)

Python SDK config:

```python
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
exporter = OTLPSpanExporter(endpoint="http://localhost:4317", insecure=True)
```

**Console exporter** (Phase 1, zero infrastructure):

```python
from opentelemetry.sdk.trace.export import ConsoleSpanExporter
```

Prints spans as JSON to stdout. No Docker, no browser.

**otel-tui** (middle ground) — terminal-based OTEL viewer ([ymtdzzz/otel-tui](https://github.com/ymtdzzz/otel-tui)). No Docker, no browser, reads OTLP directly.

## Storage observability (FileStore → SQLite validation)

Instrument storage operations with spans tagged by operation type (`save_game`, `list_games`, `get_game`, etc.) and filter complexity. This data directly informs the decision to migrate from `FileStore` to `SQLiteStore` — when `list_games` latency with filters exceeds acceptable thresholds at scale, the traces provide evidence.
