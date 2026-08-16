# MF Strategy Tester

A local-first research application for ingesting Indian mutual-fund data, defining investment strategies visually, and running reproducible point-in-time backtests.

The bootstrap currently provides:

- a typed FastAPI backend;
- SQLite persistence through SQLAlchemy and Alembic migrations;
- immutable, versioned declarative strategy definitions;
- immutable, content-addressed AMFI source capture and audited ingestion batches;
- resumable AMFI historical NAV synchronization with immutable normalized revisions;
- strict current/historical NAV, scheme-list, scheme-details, and distribution parsers;
- a React/TypeScript strategy-builder shell;
- deterministic unit and API tests;
- explicit architecture and phased delivery documentation.

It publishes provenance-linked AMFI NAV history but does **not** yet execute financial backtests.
The remaining capabilities are sequenced in [the implementation plan](docs/IMPLEMENTATION_PLAN.md).

## Architecture

```text
AMFI artifacts (immutable)       Strategy builder (React)
            |                              |
            v                              v
ingestion -> normalization -> research API / strategy versions
                         |                 |
                         +--------+--------+
                                  v
                    point-in-time backtest engine
                                  |
                                  v
                   auditable runs, ledgers, metrics
```

Canonical financial logic belongs in the backend domain/backtest layers. The web application only constructs validated strategy documents and presents results.

## Prerequisites

- Python 3.12+
- `uv`
- Node.js 22+
- npm 10+

## Run locally

```bash
uv sync --project backend --extra dev
uv run --project backend alembic -c backend/alembic.ini upgrade head
uv run --project backend uvicorn mf_strategy_tester.api.main:app --reload
```

In a second terminal:

```bash
npm --prefix web install
npm --prefix web run dev
```

The API binds to `127.0.0.1:8000` by default. The UI is available at `127.0.0.1:5173`.

## Capture official AMFI sources

Run the migration first. Each command creates an audited ingestion batch and stores the response by
SHA-256 under `MFST_RAW_DATA_PATH`. Repeated content reuses the artifact; it is never overwritten.

```bash
uv run --project backend mfst ingest current-nav
uv run --project backend mfst ingest fund-list
uv run --project backend mfst ingest historical-nav \
  --fund-id all --from-date 2026-06-01 --to-date 2026-06-30
uv run --project backend mfst ingest scheme-list --fund-id 3
uv run --project backend mfst ingest scheme-details --fund-id 3 --scheme-id 12233
uv run --project backend mfst ingest distributions --fund-id 3 --scheme-id 12233 --year All
```

Historical requests are rejected when they exceed AMFI's current 90-calendar-day limit. Fund and
scheme IDs are AMFI source identifiers; they are not treated as canonical internal scheme identity.

For the resumable historical load and a scheduler-ready daily incremental command, see
[AMFI synchronization](docs/AMFI_SYNC.md).

Resume the complete historical load from the console with:

```bash
./scripts/sync_amfi_full.sh
```

## Verify

```bash
uv run --project backend pytest backend/tests
uv run --project backend ruff check backend
uv run --project backend mypy backend/src
npm --prefix web run check
```

## Repository layout

```text
backend/  Python API, domain model, persistence, migrations, tests
web/      React strategy-builder UI and tests
docs/     architecture decisions and phased product plan
```
