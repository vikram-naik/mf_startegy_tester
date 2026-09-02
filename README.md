# MF Strategy Tester

A local-first research application for ingesting Indian mutual-fund data, defining investment strategies visually, and running reproducible point-in-time backtests.

The bootstrap currently provides:

- a typed FastAPI backend;
- SQLite persistence through SQLAlchemy and Alembic migrations;
- immutable, versioned declarative strategy definitions;
- immutable, content-addressed AMFI source capture and audited ingestion batches;
- resumable AMFI historical NAV synchronization with immutable normalized revisions;
- resumable AMFI distribution snapshot ingestion with queryable source rows and provenance;
- append-only IDCW coverage assessments that distinguish present, blocked, and unverified-empty
  source evidence;
- strict current/historical NAV, scheme-list, scheme-details, and distribution parsers;
- resumable AMFI scheme-family lifecycle/launch acquisition;
- official Nifty price/TRI/NTR and NSE/BSE ETF price acquisition with immutable revisions;
- a React/TypeScript strategy-builder shell;
- a scheme-option research view with NAV-only since-inception, rolling-return, drawdown, and
  canonical IDCW provenance drill-down;
- deterministic unit and API tests;
- explicit architecture and phased delivery documentation.

It publishes provenance-linked AMFI NAV history but does **not** yet execute financial backtests.
The remaining capabilities are sequenced in [the implementation plan](docs/IMPLEMENTATION_PLAN.md).
For the authoritative local dataset checkpoint and clean-conversation restart instructions, read
[current project status](docs/CURRENT_STATUS.md) before using older handoff numbers.

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
Open the Data view and select a scheme name to inspect its performance. Calculation conventions and
IDCW limitations are documented in [NAV-only scheme performance](docs/NAV_PERFORMANCE.md).

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

### Routine NAV and payout refresh

Run the NAV synchronization daily:

```bash
./scripts/sync_amfi_daily.sh
```

This refreshes the AMFI fund catalog, re-reads the previous seven days of NAV history to capture
source corrections, and publishes the current AMFI bulk NAV feed. It does **not** acquire IDCW
payouts.

Run the complete payout synchronization weekly:

```bash
./scripts/sync_distributions_refresh.sh
```

The wrapper refreshes the accepted sources in precedence order: official AMFI, CAMS and KFintech,
then AdvisorKhoj. It selects refresh mode for the AMFI and RTA workflows, retains their individual
reports and checkpoints, and finishes with a combined option-level coverage assessment. The
AdvisorKhoj workflow captures a new catalog and distribution snapshot on every run. This is a long
network acquisition and can run for hours.

The daily NAV script and each underlying distribution source script use the same non-blocking
`data/amfi-sync.lock`. They must not overlap. The complete refresh wrapper also uses
`data/distribution-refresh.lock` to prevent two weekly workflows from running concurrently. A lock
conflict exits with status 75 rather than allowing concurrent database writers.

For cron implementations that support `CRON_TZ`, the following example runs NAV acquisition daily
at 03:30 and payout acquisition every Sunday at 04:30, both in `Asia/Kolkata`:

```cron
CRON_TZ=Asia/Kolkata
30 3 * * * /home/vn/python-projects/mf_startegy_tester/scripts/sync_amfi_daily.sh >> /home/vn/python-projects/mf_startegy_tester/data/amfi-nav-daily.log 2>&1
30 4 * * 0 /home/vn/python-projects/mf_startegy_tester/scripts/sync_distributions_refresh.sh >> /home/vn/python-projects/mf_startegy_tester/data/distribution-refresh-weekly.log 2>&1
```

The scheduler timezone must be explicit. If the project is moved, replace the absolute paths.
Detailed synchronization and operational-check instructions are in
[AMFI synchronization](docs/AMFI_SYNC.md).

After NAV coverage is complete, capture every currently catalogued AMFI distribution family with:

```bash
./scripts/sync_amfi_distributions.sh
```

This initial mode resumes from per-scheme checkpoints. Use
`./scripts/sync_distributions_refresh.sh` for the recurring multi-source refresh.

Strict parsing remains the default. To finish surveying the source while retaining every invalid
distribution row for review:

```bash
MFST_DISTRIBUTION_QUARANTINE_RECORD_ERRORS=1 ./scripts/sync_amfi_distributions.sh
```

Such a run reports `completed_with_issues`; it does not silently treat rejected rows as ingested.
After parser support is added, set `MFST_DISTRIBUTION_RETRY_QUARANTINED=1` to retry checkpointed
families with open issues.

Generate the read-only Phase 2 data-quality acceptance report with:

```bash
uv run --project backend mfst data-quality-report
```

It distinguishes unique NAV issues from repeated batch observations and lists unresolved AMFI
distribution option identifiers without using scheme names as identity keys. See
[AMFI synchronization](docs/AMFI_SYNC.md) for the guarded stale-batch reconciliation procedure.
The evidence status and zero-value distribution gate are documented in the
[distribution identifier review](docs/DISTRIBUTION_IDENTITY_REVIEW.md).
That report also partitions every accepted source row through a conservative normalization gate;
its `cash_amount_candidate` result is an audited input set, not a canonical event or investor cash
flow.

Publish the gated candidates as provenance-linked, revisioned canonical source events:

```bash
uv run --project backend mfst normalize-distributions
```

These events retain AMFI's date as `record_date`; they are not payment-date cash flows.
Snapshot option-level evidence coverage with:

```bash
uv run --project backend mfst assess-distribution-coverage
```

The distribution synchronization script invokes both commands after successful source capture.
Coverage status meanings and the incomplete-AMFI-source evidence are documented in
[IDCW distribution coverage](docs/DISTRIBUTION_COVERAGE.md).

Optionally publish a supported HDFC declaration when paired with its official scheme-summary
identity document:

```bash
uv run --project backend mfst publish-hdfc-distribution-notice \
  --notice-url 'https://files.hdfcfund.com/...notice.pdf' \
  --scheme-summary-url 'https://files.hdfcfund.com/...scheme-summary.pdf'
uv run --project backend mfst assess-distribution-coverage
```

The command preserves both PDFs, requires explicit AMFI-code evidence, is idempotent, and blocks
amount conflicts instead of silently revising canonical data. Broad AMC-notice collection is
supplemental documentary provenance; it is not an IDCW acquisition-completion requirement for this
local research dataset.

Capture and import all discoverable CAMS/KFintech IDCW histories after the AMFI pass:

```bash
./scripts/sync_rta_distributions.sh
```

RTA proprietary codes are never treated as AMFI codes. Automatic mappings require exact
scheme-core, plan, and NAV evidence; unresolved rows and amount conflicts remain blocked and
queryable. See [CAMS and KFintech IDCW capture](docs/RTA_DISTRIBUTIONS.md) for resumability,
operator queries, manual-review commands, and source limitations.

Run the complete, non-publishing AdvisorKhoj secondary-source acquisition with:

```bash
./scripts/sync_advisorkhoj_distributions.sh
```

The workflow discovers the current public catalog, captures every listed scheme history, verifies
amount/NAV/yield arithmetic, and persists only AMFI mappings backed by compatible option qualifiers
and a conflict-free multi-date NAV fingerprint. It does not publish canonical events. See
[AdvisorKhoj secondary distribution acquisition](docs/ADVISORKHOJ_DISTRIBUTIONS.md) for the trust
boundary, resumability, live full-catalog result, and remaining identity-review backlog.

Acquire current AMFI family lifecycle facts, then official benchmark series:

```bash
./scripts/sync_scheme_lifecycle.sh
./scripts/sync_nifty_benchmarks.sh
./scripts/sync_etf_prices.sh
```

These are restart-safe long batches. Run them one at a time and retain the printed JSON report
paths. See [scheme lifecycle acquisition](docs/SCHEME_LIFECYCLE.md) and
[benchmark acquisition](docs/BENCHMARK_ACQUISITION.md) for source and completeness boundaries.

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
