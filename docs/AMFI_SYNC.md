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

AMFI currently publishes explicit `Plan` and `Option` columns in both current and historical NAV
feeds. Parser `amfi-2026.08.8` uses exact header-specific field maps for those contracts and retains
support for the earlier six-field current and eight-field historical artifacts. Explicit qualifiers
drive normalized metadata when present; blank fields fall back independently to the legacy name
heuristic, and unrecognized nonblank labels remain `unknown`. Unknown headers or row widths are
fatal rather than being positionally guessed.

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

For an AMFI-only recovery or diagnostic run, use:

```bash
./scripts/sync_amfi_daily.sh
```

It defaults to today's date in `Asia/Kolkata`. Each run re-reads the previous seven days before
advancing, allowing AMFI corrections to be captured as revisions. Override the overlap only when
there is an explicit operational reason:

```bash
MFST_SYNC_OVERLAP_DAYS=14 ./scripts/sync_amfi_daily.sh
```

For the routine scheduler, use the unified daily instrument workflow instead:

```bash
./scripts/sync_all_daily.sh
```

It refreshes AMFI catalog/NAV, official Nifty index series, and NSE/BSE ETF prices sequentially
under the same acquisition lock. `MFST_DAILY_OVERLAP_DAYS` controls the shared correction window;
`MFST_DAILY_END_DATE` provides an explicit reproducible cutoff. Per-lane JSON reports and the
aggregate log/status are retained under `data/daily-sync-reports/`.

### Cron example (03:30 India time)

The host scheduler's timezone must be explicit. On cron implementations supporting `CRON_TZ`, add
the following with `crontab -e`, replacing the project path if needed:

```cron
CRON_TZ=Asia/Kolkata
30 3 * * * /home/vn/python-projects/mf_startegy_tester/scripts/sync_all_daily.sh
```

The entrypoint acquires a non-blocking `flock` on `data/amfi-sync.lock`. A scheduler invocation that
overlaps an active run exits with status 75 instead of starting a second writer.

## Operational checks

The latest run and per-fund checkpoints are recorded in `nav_sync_runs` and
`nav_sync_checkpoints`; committed date intervals are recorded in `nav_sync_coverage`. A run is
`completed` only after all selected funds and the current AMFI bulk feed have been captured and
published. Raw artifacts under `data/raw` are immutable and may be used to rebuild normalized data.

The current implementation covers the authoritative fund catalog and NAV observations. AMFI IDCW
history is captured by the separate distribution synchronization workflow below. Only exact-ID,
positive cash-amount candidates with explicit distribution-label evidence can publish canonical
events; inconsistent units and unresolved identifiers remain blocked.

## Distribution/IDCW source synchronization

Run the resumable initial source load only after complete NAV ingestion:

```bash
./scripts/sync_amfi_distributions.sh
```

For each active fund house, the command captures the current AMFI scheme-list response and requests
the `All` distribution snapshot for each returned family-level `scheme_id`. Successful empty
responses create checkpoints, distinguishing a verified no-payout response from a request that was
never completed. Re-running initial `full` mode skips those checkpoints.

For the recurring multi-source distribution refresh:

```bash
./scripts/sync_distributions_refresh.sh
```

The wrapper runs AMFI with `MFST_DISTRIBUTION_SYNC_MODE=refresh`, CAMS/KFintech with
`MFST_RTA_CAPTURE_MODE=refresh`, and then captures a new AdvisorKhoj catalog and payout snapshot. It
runs the accepted sources in precedence order and finishes with a combined distribution coverage
assessment. Timestamped workflow logs and final step statuses are retained under
`data/distribution-refresh-reports/`. This is the scheduler entrypoint for the weekly payout refresh
and may run for hours.

The NAV and distribution scripts share `data/amfi-sync.lock` and must not overlap. In strict mode, a
run is complete only when the latest `distribution_sync_runs` row is `completed` and
`schemes_completed` equals `schemes_total`. Each completed family has a row in
`distribution_sync_checkpoints`, including families whose source snapshot contained zero rows.

Strict parsing fails on the first invalid row. For an explicit source-survey run that commits valid
rows and quarantines row-level parse failures:

```bash
MFST_DISTRIBUTION_QUARANTINE_RECORD_ERRORS=1 ./scripts/sync_amfi_distributions.sh
```

Transport failures, invalid JSON/envelopes, and request-identity mismatches remain fatal. Each row
failure is stored in `distribution_parse_issues` with its immutable batch, source family, 1-based
record number, raw JSON, parser error, and canonical raw-record signature. Batch and checkpoint
counts must satisfy `rows_received = rows_accepted + rows_rejected`. A run with open issues finishes
as `completed_with_issues`, which is deliberately distinct from clean completion.

After adding parser support, retry only checkpointed families with open issues while continuing to
quarantine any still-unknown forms:

```bash
MFST_DISTRIBUTION_QUARANTINE_RECORD_ERRORS=1 \
MFST_DISTRIBUTION_RETRY_QUARANTINED=1 \
./scripts/sync_amfi_distributions.sh
```

An issue becomes `resolved` only when a later accepted row has the same canonical raw-record
signature. A changed or removed upstream row does not silently resolve the historical issue.

Monitor the latest persisted run without interfering with the writer:

```bash
sqlite3 -readonly -header -column data/research.db "
SELECT status,
       record_error_policy,
       funds_completed || '/' || funds_total AS funds,
       schemes_completed || '/' || schemes_total AS schemes,
       rows_received, rows_inserted, rows_rejected, rows_unresolved,
       completed_at, error_details
FROM distribution_sync_runs
ORDER BY started_at DESC
LIMIT 1;
"
```

Review all currently open row issues:

```bash
sqlite3 -readonly -header -column data/research.db "
SELECT mutual_fund_id, source_scheme_id, record_number,
       issue_code, error_details, raw_record, ingestion_batch_id
FROM distribution_parse_issues
WHERE status = 'open'
ORDER BY mutual_fund_id, source_scheme_id, record_number;
"
```

AMFI distribution rows contain both the selected family-level `scheme_id` and an option-level
`SD_ID`. The source records retain both. Exact `SD_ID = scheme_options.amfi_scheme_code` matches can
be used for reconciliation; an absent match remains explicitly unresolved and is never replaced by
a scheme-name guess.

The current distribution response also includes nullable `Plan` and `Option` fields. Parser
`amfi-2026.08.10` introduced support for either the exact legacy object shape or that
qualifier-aware shape, normalizes null/blank qualifiers to missing, and preserves every nonblank
value in queryable source rows. Parser `amfi-2026.08.11` retains those contracts and adds the
strict, timezone-aware scheme-details contract used by lifecycle acquisition. Migration
`20260821_0018` adds the nullable source columns. Because source rows are immutable,
the qualifier-aware refresh creates a second source-row version for a legacy row; canonical event
normalization remains idempotent and links both versions without creating a second event.

`Rate_of_div` is always stored as exact source text. Scalar records also retain their decimal
magnitude. AMFI historical responses may include an explicit trailing `%`; that marker takes
precedence and is retained. Otherwise, in accordance with AMFI's published note, scalar records
through 06-Apr-2009 are labelled `percentage`, and later records are labelled `amount`.

AMFI also returns at least one non-scalar `1:3` value. It is stored as unit `ratio`, with separate
positive numerator and denominator fields and no scalar `source_value`. Taurus AMC independently
documents that exact October 2003 observation as a bonus ratio. The source layer does not infer a
canonical event type from ratio syntax. No scale conversion, reinvestment, payment-date inference,
or cash-flow publication is performed.

One historical percentage is published as `20% (Rs 2/- Per Unit` (including the unmatched source
parenthesis). It retains scalar percentage `20` and a separate
`annotated_amount_per_unit_inr = 2`. The annotation is not promoted to a canonical distribution
amount or payment cash flow. AMFI also varies the capitalization of `Per`/`per`; only those literal
phrase-case variations are accepted, while the numeric/currency grammar remains strict.

The complete survey also found one dash-form value, `15%-Rs 1.50 Per Unit`. It is represented by
the same percentage-plus-annotation fields. Only the observed dash grammar is accepted; the raw
source text remains immutable.

The scheme-list endpoint is a current source universe. Removed historical families may be absent,
so completion of this sync is not evidence of a survivorship-free distribution universe.

## Phase 2 data-quality acceptance report

Generate the consolidated, read-only report after NAV and distribution synchronization:

```bash
uv run --project backend mfst data-quality-report
```

For NAV issues, `distinct_observations` deduplicates by issue code, severity, AMFI scheme code, and
NAV date. `occurrence_count` retains the number of batch-level issue rows, so repeat downloads stay
visible without overstating the number of affected financial observations. The report separately
lists every distribution `source_option_id` absent from `scheme_options`, including source family,
names, row count, and record-date range. Names are investigation evidence only; they are not an
identity-resolution rule.

The same report applies the conservative normalization-candidate gate documented in
[the distribution identifier review](DISTRIBUTION_IDENTITY_REVIEW.md). `cash_amount_candidate`
means only that an exact-ID, positive amount row has explicit dividend/IDCW source-label evidence.
It is not a published cash flow. All other rows remain separated by blocking reason, and
label-based blockers include their source names for review.

After reviewing the gate, publish only its candidates into the revisioned canonical event model:

```bash
uv run --project backend mfst normalize-distributions
```

The command is idempotent and retains an exact source-row link for every canonical revision. It
does not infer payment dates or create portfolio cash flows.

After normalization, snapshot what the current evidence establishes for every IDCW option:

```bash
uv run --project backend mfst assess-distribution-coverage
```

The append-only assessment distinguishes options with canonical events, options with only blocked
source rows, and options for which AMFI returned no exact-code distribution rows. An empty response
is `unverified_empty`, never confirmed as a zero-payout history. See
[IDCW distribution coverage](DISTRIBUTION_COVERAGE.md).

`scripts/sync_amfi_distributions.sh` runs normalization and the coverage assessment automatically
after a successful source synchronization. The standalone commands remain useful for audited
replay and recovery.

Record an idempotent, append-only `source_only` review for each unmatched ID after the survey:

```bash
uv run --project backend mfst survey-distribution-identifiers
```

The review references the exact completed AMFI ingestion batch and immutable artifact containing
the ID. It does not resolve the ID to a NAV option. Later `mapped` or `source_error` reviews require
a different completed evidence artifact; the command rejects the distribution artifact itself as
proof of a cross-identifier conclusion. See
[the distribution identifier review](DISTRIBUTION_IDENTITY_REVIEW.md).

An interrupted process can leave an `ingestion_batches.status = 'running'` row. Do not update it
with ad-hoc SQL. First verify that no worker owns the shared lock and that no lower-level ingestion
process is active. Then close only the exact abandoned batch with a timezone-aware cutoff and an
auditable reason:

```bash
uv run --project backend mfst reconcile-stale-batch \
  --batch-id 00000000-0000-0000-0000-000000000000 \
  --stale-before 2026-08-17T00:00:00+00:00 \
  --reason "Interrupted worker was verified absent"
```

The command refuses non-running batches, naive timestamps, batches at or after the cutoff, and
empty reasons. It changes only batch status, completion time, and error details; source artifacts
and published observations remain unchanged.
