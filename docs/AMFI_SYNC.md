# AMFI historical and daily NAV synchronization

## Scope and correctness conventions

The synchronizer discovers the current AMFI fund catalog, requests each fund's NAV history in
at most 90-calendar-day windows, stores every response immutably, and publishes provenance-linked
NAV revisions. The initial lower bound is `2006-04-01`, the earliest date found during explicit
AMFI endpoint probing; empty earlier responses are preserved as evidence, not interpreted as NAV
observations.

An AMFI scheme code is the stable option-level key. Scheme names, fund-house labels,
classifications, ISINs, and conservatively inferred plan/option labels are versioned metadata—not
identifiers. A revised source value creates a new NAV revision and retires the former current
revision. Missing dates are never created or forward-filled. Non-positive numeric NAVs are retained
with an error status and excluded from valid research observations. Non-numeric source sentinels
such as AMFI's historical `#DIV/0!` values are linked to a data-quality issue but never published
as a NAV observation.

The checkpoint for each AMFI fund advances only after raw capture, strict parsing, normalized
publication, and commit all succeed for a date window. A failed run can therefore be rerun safely.

## Initial historical load

Install dependencies, then start the checked-in resumable load:

```bash
uv sync --project backend --extra dev
./scripts/sync_amfi_full.sh
```

AMFI limits a historical request to 90 days and requires a fund ID, so the complete load consists
of thousands of official requests and may run for hours on a local machine. Re-running the same
script resumes from committed coverage rather than downloading completed windows again. The script
also runs pending migrations and acquires `data/amfi-sync.lock`, preventing overlap with the daily
job. `Ctrl+C` stops safely: committed raw artifacts, normalized records, and checkpoints remain
available to the next run.

To reproduce a historical cutoff instead of defaulting to today's date in `Asia/Kolkata`:

```bash
MFST_SYNC_END_DATE=2026-08-16 ./scripts/sync_amfi_full.sh
```

Use the lower-level `mfst sync-nav --fund-id 3` option (repeatable) only for targeted validation or
recovery.

## Daily incremental load

Run the checked-in entrypoint manually:

```bash
./scripts/sync_amfi_daily.sh
```

It defaults to today's date in `Asia/Kolkata`. Each run re-reads the previous seven days before
advancing, allowing AMFI corrections to be captured as revisions. Override the overlap only when
there is an explicit operational reason:

```bash
MFST_SYNC_OVERLAP_DAYS=14 ./scripts/sync_amfi_daily.sh
```

### Cron example (03:30 India time)

The host scheduler's timezone must be explicit. On cron implementations supporting `CRON_TZ`, add
the following with `crontab -e`, replacing the project path if needed:

```cron
CRON_TZ=Asia/Kolkata
30 3 * * * /home/vn/python-projects/mf_startegy_tester/scripts/sync_amfi_daily.sh >> /home/vn/python-projects/mf_startegy_tester/data/amfi-sync.log 2>&1
```

The entrypoint acquires a non-blocking `flock` on `data/amfi-sync.lock`. A scheduler invocation that
overlaps an active run exits with status 75 instead of starting a second writer.

## Operational checks

The latest run and per-fund checkpoints are recorded in `nav_sync_runs` and
`nav_sync_checkpoints`; committed date intervals are recorded in `nav_sync_coverage`. A run is
`completed` only after all selected funds and the current AMFI bulk feed have been captured and
published. Raw artifacts under `data/raw` are immutable and may be used to rebuild normalized data.

The current implementation covers the authoritative fund catalog and NAV observations. AMFI IDCW
history requires a separate scheme-to-option reconciliation workflow because its endpoint uses a
different scheme identifier and historical values do not use one consistent unit. Those records
must not be normalized into cash distributions until that mapping and unit interpretation are
auditable.
