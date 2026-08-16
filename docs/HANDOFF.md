# Project handoff

**Snapshot date:** 2026-08-16 (`Asia/Kolkata`)

**Branch:** `main`

**Baseline:** initial repository commit created from this handoff

## Product objective

Build a local-first Indian mutual-fund research application where a user can ingest authoritative
AMFI data, visually define a versioned investment strategy or portfolio, and run reproducible,
point-in-time backtests without strategy-specific code changes.

Correctness, source provenance, stable identifiers, explicit timing conventions, and auditability
take precedence over UI convenience. `AGENTS.md` contains the governing engineering and financial
rules for subsequent sessions.

## Current implementation

### Foundation and strategy catalog

- FastAPI backend with typed Pydantic boundaries.
- SQLite persistence, WAL mode, foreign-key enforcement, and six Alembic migrations.
- Immutable, versioned declarative strategy definitions.
- React/TypeScript/Vite strategy-builder shell.

### AMFI ingestion and normalized NAV data

- Strict adapters for the AMFI fund catalog, current NAV feed, historical NAV windows, scheme
  lists/details, and distribution source capture.
- Immutable SHA-256-addressed raw artifacts and audited ingestion batches.
- AMFI scheme code as the stable option-level identifier; names and classifications are versioned
  metadata.
- Decimal-preserving NAV publication, immutable revisions, source lineage, and quarantine records.
- Resumable historical coverage intervals and per-fund-house checkpoints.
- Full and incremental console entrypoints with a shared non-overlap lock.
- `Ctrl+C` records the run as failed while preserving all committed artifacts, observations, and
  checkpoints.

### Research data UI

The Data view provides:

- AMFI fund-house selection;
- stored scheme-option counts;
- exact AMFI classification filters;
- Direct/Regular and Growth/IDCW/Bonus filters;
- search by scheme name, AMFI scheme code, or ISIN;
- first/latest stored NAV dates, latest NAV, data-quality status, and coverage state;
- the latest ingestion-run summary and immutable source-capture ledger.

The browser uses metadata attached to each scheme option's latest stored NAV record. Financial
metrics are not calculated in the frontend.

## Local dataset snapshot

The local `data/` directory is intentionally ignored by Git and was approximately **4.5 GB** at
handoff. It remains available in this workspace but is not part of a fresh clone.

| Measure | Value |
| --- | ---: |
| AMFI fund-house catalog entries marked active | 57 |
| Stored AMFI scheme options | 16,640 |
| Valid current NAV revision rows | 5,748,206 |
| Quarantined/error current NAV rows | 46,976 |
| Earliest valid NAV date | 2006-04-01 |
| Latest valid NAV date | 2026-08-16 |
| Fully covered fund houses | 4/57 |

“Fully covered” means one committed `nav_sync_coverage` interval spans the complete requested
window from `2006-04-01` through `2026-08-16`. A checkpoint at the end date is insufficient if an
earlier gap remains.

The four fully covered fund houses are:

- JM Financial Mutual Fund (`16`)
- Kotak Mahindra Mutual Fund (`17`)
- LIC Mutual Fund (`18`)
- quant Mutual Fund (`13`)

The last full sync was intentionally interrupted and is recorded as `failed` with committed state
preserved. No ingestion worker or review server should be running at this handoff.

## Start the application

Install or restore dependencies and migrate:

```bash
uv sync --project backend --extra dev
uv run --project backend alembic -c backend/alembic.ini upgrade head
npm --prefix web install
```

Start the API:

```bash
uv run --project backend uvicorn mf_strategy_tester.api.main:app --reload
```

Start the UI in another terminal:

```bash
npm --prefix web run dev
```

Open `http://127.0.0.1:5173/?view=data` for the research-data browser.

## Resume ingestion

Run the complete historical synchronization in the foreground:

```bash
./scripts/sync_amfi_full.sh
```

The script runs migrations, acquires `data/amfi-sync.lock`, and requests only uncovered windows.
It defaults to today's date in `Asia/Kolkata`. To use an explicit reproducible cutoff:

```bash
MFST_SYNC_END_DATE=2026-08-16 ./scripts/sync_amfi_full.sh
```

After the historical load is complete, use the incremental entrypoint manually or from a local
scheduler:

```bash
./scripts/sync_amfi_daily.sh
```

Do not run the full and daily scripts concurrently. The lock rejects an overlapping invocation
with exit status 75.

## Verification commands

```bash
uv run --project backend pytest backend/tests
uv run --project backend ruff check backend
uv run --project backend ruff format --check backend
uv run --project backend mypy backend/src
npm --prefix web run check
bash -n scripts/sync_amfi_full.sh scripts/sync_amfi_daily.sh
```

Handoff verification result:

- 34 backend tests passed.
- Ruff lint and formatting checks passed.
- mypy strict checks passed.
- Three frontend tests and the TypeScript/Vite production build passed.
- Live fund-house query: approximately 0.07 seconds on the handoff database.
- Live filtered scheme page: approximately 0.11 seconds on the handoff database.

## Phase status and next-session order

Phase 0 and Phase 1 are implemented. Phase 2 is **not complete**.

Next session should proceed in this order:

1. Run `./scripts/sync_amfi_full.sh` until all intended fund houses have contiguous coverage through
   the cutoff date.
2. Review completeness independently; do not infer completeness from total row count or a final
   checkpoint alone.
3. Investigate catalogued fund houses with zero stored scheme options, coverage gaps, invalid NAVs,
   stale current-feed records, suspicious one-day moves, and scheme-code/ISIN inconsistencies.
4. Add observation-level NAV/provenance and data-quality drill-down to the UI.
5. Resolve scheme lifecycle evidence—renames, mergers, closures, and inactive options—before making
   survivorship-free claims.
6. Design IDCW/distribution normalization only after AMFI scheme-identifier reconciliation and the
   historical unit-convention change are auditable.
7. Begin Phase 3 portfolio accounting only after the Phase 2 data gate is explicitly accepted.

## Known limitations and risks

- The 57 AMFI catalog entries are provider/fund-house identifiers, not 57 schemes. AMFI catalog
  presence alone does not prove that every provider is economically active throughout history.
- Current database coverage is incomplete. Existing scheme and row counts mix current-feed and
  partial historical observations.
- AMFI classification strings contain historical formatting variants and are retained faithfully;
  canonical category mapping is not implemented.
- Distribution/IDCW source capture exists, but normalized cash distribution events do not.
- Complete scheme lifecycle and merger resolution is not implemented.
- Point-in-time information-availability timestamps remain an explicit future engine concern; a
  NAV date alone does not prove when the NAV was knowable.
- The portfolio accounting engine, backtest orchestration, metrics, benchmark series, run ledger,
  and research-result UI are not implemented.
- Local source artifacts and `research.db` are intentionally absent from Git. Back up `data/`
  separately before destructive storage or migration work.

## Important files

- `AGENTS.md` — mandatory correctness and working rules.
- `README.md` — local setup and repository overview.
- `docs/IMPLEMENTATION_PLAN.md` — phase plan and exit criteria.
- `docs/ARCHITECTURE.md` — accepted architecture decisions and invariants.
- `docs/AMFI_SYNC.md` — ingestion conventions and scheduler usage.
- `backend/src/mf_strategy_tester/services/nav_sync.py` — resumable synchronization behavior.
- `backend/src/mf_strategy_tester/services/nav_publication.py` — normalized NAV publication.
- `backend/src/mf_strategy_tester/api/routes/data.py` — coverage and scheme-browser queries.
- `web/src/components/DataWorkspace.tsx` — research-data browser.
- `scripts/sync_amfi_full.sh` — complete historical resume entrypoint.
- `scripts/sync_amfi_daily.sh` — incremental scheduled entrypoint.
