# Current project status

**Authoritative conversation-reset checkpoint:** 2026-09-04 (`Asia/Kolkata`)
**Branch:** `main`
**Repository migration head:** `20260928_0034`
**Local dataset migration applied:** `20260904_0032` (as of the 2026-09-04 checkpoint)

Read `CLAUDE.md` before changing code. This file supersedes older numeric status and next-step
statements in `docs/HANDOFF.md`; the older handoff remains useful as a chronological audit log.

## Operator decisions that must be preserved

- Required IDCW acquisition sources are AMFI first, CAMS and KFintech as equal-priority RTA
  fallbacks, and AdvisorKhoj as tertiary fallback.
- Broad official-AMC notice acquisition is optional and is not an acquisition-completion gate.
  The user does not require documentary evidence beyond the accepted source policy.
- Missing or structurally blank responses must remain unknown. Never convert them to a zero payout
  or a verified empty history.
- Do not map a source scheme to an AMFI option by name alone. Require the repository's explicit
  identity evidence policy.
- Every long-running acquisition, synchronization, backfill, build, or analysis must be provided as
  a resumable script for the user to run in a separate console. It must retain timestamped logs,
  progress/checkpoints, and a final status/report for later inspection. The agent may run bounded
  tests and read-only diagnostics only. See `CLAUDE.md` section 39.
- Phase 2 was committed as baseline `4fc7302`; lifecycle recovery and aligned documentation were
  committed as `9b93ed7`. The ignored `data/` directory is about 40 GB and remains valuable local
  research data; do not reset, clean, or delete it.
- The product direction is now a customized cross-fund-house performance screener. Strategy
  construction, portfolio accounting, and backtesting are out of scope.

## Completion decision

Accepted-source IDCW acquisition is operationally complete with explicit source limitations. Do
not run another full CAMS, KFintech, AdvisorKhoj, lifecycle, or benchmark acquisition merely to
reproduce the current checkpoint.

“Complete” here means that the accepted source lanes were traversed and their successes, failures,
raw responses, mappings, and reports were retained. It does not certify complete payout history for
every option. `unverified_empty` remains absence of resolved evidence, not proof of no payout.

There are no ingestion batches currently marked `running`.

## Local dataset snapshot

### NAV and schema

| Measure | Current value |
| --- | ---: |
| Scheme options | 33,277 |
| Valid current NAV revisions | 33,690,415 |
| Error/quarantined current NAV revisions | 343,296 |
| Valid NAV date range | 2006-04-01 through 2026-09-01 |
| Locally applied Alembic revision | `20260904_0032` |

Incremental NAV run `776d0e37-698c-4c87-b766-c7188d137c10` completed all 57 fund houses on
2 September through the requested cutoff of 2 September. It received 55,559 rows, inserted 8,204,
classified 47,355 unchanged, created no financial revisions, and quarantined 691 non-positive
rows. All 57 fund checkpoints are complete through 2 September; the newest valid source NAV is
dated 1 September.

### Canonical IDCW data

The final accepted-source coverage report is
`data/rta-reports/coverage-20260901T001442Z.json`, run
`1fad288c-2d38-4048-8f0d-7831bdf0ac8a`.

| Measure | Current value |
| --- | ---: |
| Canonical distribution events | 875,315 |
| Canonical revisions | 882,424 |
| Provenance links | 1,084,066 |
| Canonical event date range | 1990-06-01 through 2026-08-30 |
| IDCW-classified options assessed | 16,432 |
| `events_present` | 3,932 |
| `blocked_source_rows` | 67 |
| `unverified_empty` | 12,433 |
| Open distribution parse issues | 0 |

Coverage statuses are evidence states, not completeness percentages. In particular, the 12,433
`unverified_empty` IDCW-classified options have no resolved accepted-source row; they are not known
zero-payout options.

The 67 `blocked_source_rows` options were audited with the read-only
`mfst distribution-blocker-report` command against coverage run
`1fad288c-2d38-4048-8f0d-7831bdf0ac8a`. All 937 blocked rows are exact-code AMFI rows; no
official-notice, RTA, or AdvisorKhoj row is present in this blocked set. The existing ordered
normalization gate partitions them as follows:

| Blocker | Rows | Options affected |
| --- | ---: | ---: |
| Percentage source value | 381 | 62 |
| Ambiguous Bonus/Dividend option | 528 | 2 |
| Non-positive scalar | 28 | 6 |

Option counts overlap because one option can contain different row-level blockers. The categories
are disjoint at row level and sum to 937. None can be converted safely to an INR-per-unit cash
event without additional official evidence, so the audit made no canonical-data or identity
change.

### RTA capture and latest identity state

These counts use only the latest mapping review per capture. Append-only issue/review occurrence
counts in a data-quality report can grow after idempotent re-imports and must not be interpreted as
unique outstanding identities.

| Provider | Captures | Source rows | Mapped | Ambiguous | Unresolved |
| --- | ---: | ---: | ---: | ---: | ---: |
| CAMS | 1,658 | 40,849 | 1,278 | 3 | 377 |
| KFintech | 4,107 | 643,469 | 748 | 108 | 3,251 |

The read-only `mfst distribution-identity-backlog-report` now reports unique latest reviews rather
than append-only review occurrences. The 2026-09-01 live report found:

| Provider | Nonempty unresolved captures | Unresolved source rows | Nonempty ambiguous captures | Ambiguous source rows | Empty unresolved captures |
| --- | ---: | ---: | ---: | ---: | ---: |
| CAMS | 377 | 8,566 | 3 | 52 | 0 |
| KFintech | 2,867 | 347,564 | 108 | 55,505 | 384 |
| AdvisorKhoj | 396 | 225,701 | 27 | 78,749 | 1,776 |

Across providers this is 3,778 nonempty backlog captures and 716,137 retained source rows. These
are capture-row impact counts, not unique economic payout events. Every unresolved RTA capture has
zero exact name/plan/NAV candidates, and every ambiguous RTA capture has multiple candidates. For
AdvisorKhoj, 375 unresolved captures have comparable-date NAV conflicts, 19 have insufficient NAV
evidence, and two have no metadata candidate; all 27 ambiguous captures have multiple qualifying
fingerprints. The retained evidence therefore supports no new automatic or manual mapping by
itself. An AMFI code, ISIN, or official predecessor/merger artifact is still required.

The final KFintech breadth run completed the roster traversal and added 1,730 captures, 171,214
source rows, and 24,304 canonical events. It left 226 checksum-retained scheme responses with no
recognized result or explicit empty marker, plus seven fund pages with no scheme roster. Those are
source limitations, not empty histories.

The only transport failure from that traversal—fund `176`, scheme `TO#RD`—was retried with
`scripts/retry_kfintech_timeout.sh`. Status
`data/rta-reports/kfintech-targeted-retry-20260901T001442Z.status` is `exit_status=0`. KFintech
returned two valid source rows dated 2011-01-12 and 2011-04-13. They remain blocked because the
legacy Principal FMP name has no AMFI identity candidate in the local master. Do not invent a
mapping.

The final CAMS breadth report retained 997 prior scheme failures, 54 new scheme failures, and fund
page timeouts for ASK, ICICI Prudential, and Unifi while continuing across the remaining roster.
Their responses/failures remain auditable source limitations. AdvisorKhoj is the accepted broad
tertiary fallback; another unchanged full CAMS run is not recommended.

### NAV-fingerprint remap: first run and pending corrective run (2026-09-28)

The statement above that every unresolved RTA capture has zero candidates describes the
name-first rule only. Code now falls back to a NAV-fingerprint identity rule when no name candidate
exists (see `docs/RTA_DISTRIBUTIONS.md`); published amounts remain the RTA's declared values.

The first run (`rta-nav-fingerprint-2026.09.1`, `kfintech-full.jsonl` only, normalization run
`215c30c6-5090-483e-a6ef-cb4ad3436566`) moved nonempty unmapped KFintech captures from 3,422 to
3,015 and inserted 66,225 events. On 65,911 option/date pairs shared with AdvisorKhoj, 65,677
amounts were identical. It also exposed a defect: six fingerprint captures, all daily/weekly IDCW
options of liquid, ultra-short, or overnight funds (Baroda BNP Paribas, Canara Robeco, JM),
matched other plans' near-constant NAV series. Their disagreeing rows retired 9,750 existing
CAMS/KFintech values, and code `138287` disagreed with AdvisorKhoj on all 63 shared dates. Rule
`2026.09.2` rejects low-information NAV series, requires declared-amount corroboration, and never
lets a fingerprint row retire an existing RTA value. `reconcile-rta-nav-fingerprint` retires values
backed only by withdrawn fingerprint identities and restores displaced values.

Pending: after a database backup, run offline

```bash
./scripts/remap_rta_nav_fingerprint.sh
```

It re-evaluates every KFintech capture file, including the 5 September refresh and targeted retry
files that the first run did not touch, then reconciles. Until it runs, the local canonical
dataset contains the first run's defect.

The first run's 2025 payout-gap report found 4,914 IDCW options live since 2025-01-01, of which
1,977 had a declared payout since that date. SBI, UTI, Tata, Kotak, and Nippon account for 38% of
the 2,937 options without one; SBI had 13 of 303. These are acquisition gaps, not evidence of no
payout.

### AdvisorKhoj tertiary source

| Measure | Current value |
| --- | ---: |
| Catalog histories captured | 4,442 |
| Immutable source rows | 1,066,137 |
| Latest mapped histories | 2,243 |
| Latest ambiguous histories | 27 |
| Latest unresolved histories | 2,172 |
| Empty histories within unresolved | 1,776 |
| Nonempty unresolved histories | 396 |

The audited tertiary publisher has run. Its accepted rows can publish only when no higher-priority
AMFI, official-notice, CAMS, or KFintech evidence conflicts.

## Other acquisition lanes

### Scheme lifecycle

The original full run `9c86a616-669c-40c1-9177-2b2845d4862b` completed with issues:

- 57/57 fund scheme-list snapshots completed;
- 10,945/10,981 family detail rows and launch events acquired;
- 36 scheme-detail responses were rejected by the prior parser;
- no conflicting launch dates were detected.

A checksum-backed audit of all 36 retained official artifacts found the same narrow condition: the
response identity and structure are valid, but `Launch_Date` is JSON `null`. Migration
`20260902_0028` and parser `amfi-2026.09.1` retain those details as unknown-date checkpoints,
record `missing_launch_date`, and publish no launch event.

Targeted recovery run `6ac738f7-5a73-43ce-b15c-fed6b5f4489e` completed successfully at the process
level (`exit_status=0`) and met every expected postcondition:

- 36/36 detail rows inserted; zero failed or rejected;
- 10,981/10,981 latest catalog families now have detail checkpoints;
- 10,945 families have explicit launch events and 36 have explicit unknown launch evidence;
- no conflicting launch dates and no new inferred lifecycle events;
- 76 official current catalog names changed without an effective date.

The 76 names remain current attributes only. Follow-up alignment run
`9eeb4e1f-bf94-4c1e-a1f7-11a7acc8f29d` skipped all 4,004 selected details, completed with an empty
issue set, and appended no duplicate name-change issues. A direct database reconciliation found
zero checkpoint names differing from the latest immutable list snapshots. No lifecycle recovery
operator action remains.

No predecessor, merger, maturity, or closure mapping is justified by these artifacts. Several
family names look legacy or provisional, but name text plus an unknown launch date is not stable
continuity evidence. Merger, closure, predecessor identity, and survivorship-free historical-
universe evidence therefore remain incomplete.

### Benchmarks

Official Nifty index and NSE/BSE ETF acquisition completed with issues. The authoritative report is
`data/benchmark-reports/coverage-20260827T142937Z.json`.

| Series | Instruments | Observations | Date range |
| --- | ---: | ---: | --- |
| Price index | 6 | 37,857 | 1990-07-03 through 2026-08-24 |
| Gross total-return index | 6 | 35,824 | 1995-01-01 through 2026-08-24 |
| Net total-return index | 6 | 9,510 | 2000-01-03 through 2026-08-24 |
| ETF | 445 | 640,840 | 2006-05-18 through 2026-08-27 |

Residual benchmark issues are retained rather than repaired heuristically: historical BSE identity
is largely provisional, the official BSE roster is partial, some identities remain unresolved,
and invalid/empty exchange rows are quarantined or recorded. Do not treat price, GTR, and NTR as
interchangeable benchmark series.

## Current screener implementation and next work

The application is now a mutual-fund screener, not a strategy builder or backtester. The working
change removes the empty saved-strategy subsystem. Migrations `20260902_0030` through
`20260904_0032` are applied locally. No saved strategy record was deleted and no AMFI source
classification was rewritten.

The first screener release is implemented in the working tree:

- the classification response and home-screen facets distinguish ordinary mutual funds, index
  funds, ETFs, and user-created aliases that mix those product types; ETF and index-fund rows
  remain AMFI Growth-option NAV comparisons rather than exchange-price comparisons;
- the screener can select a locally acquired official Nifty price, gross total-return, or net
  total-return series as an explicit standalone reference, showing actual endpoints, return basis,
  staleness, and unavailable reasons without changing fund rank;
- the home page ranks Growth options within one selected local alias across all or one fund house,
  keeping Direct and Regular plans separate;
- migration `0030` retains 222 historical AMFI labels as approved aliases to 103 stable canonical
  classifications; the source reference contains 93 Direct Growth canonical IDs while preserving
  every source string in immutable metadata;
- migration `0031` adds 25 local screener aliases over 57 canonical classifications, with an
  immutable initial revision for each alias; the 103 canonical classifications and 222 retained
  AMFI labels are unchanged;
- migration `0032` preserves all reviewed and user-created mappings and adds singleton aliases for
  every remaining active canonical classification, so the screener dropdown and **Aliases** screen
  use one consistent alias vocabulary;
- the screener classification selector is faceted by backend-derived scheme structure and omits
  classifications without an eligible option for the current plan, horizon, endpoint tolerance,
  and optional fund-house filter; for Direct Growth over one year as of 2 September 2026 it returns
  54 concise choices instead of 77 semantically split canonical choices;
- 1-month, 3-month, 6-month, 1-year, 3-year, 5-year, and 10-year periods use exact displayed NAV
  endpoints, a seven-calendar-day tolerance, absolute return below one year, and actual/365 CAGR
  from one year onward;
- up to five funds from the same resolved local alias group can be compared on normalized-to-100
  or raw NAV charts;
- canonical IDCW cash payouts are marked at record date and are not added to NAV-only returns;
- aggregate exclusion counts expand into a paginated evidence table identifying each excluded
  option, reason, first/latest NAV dates, latest NAV value, and endpoint staleness;
- the top-level **Aliases** screen creates and edits concise names and mappings, requires a change
  reason, preserves immutable revisions, and rejects stale concurrent edits; and
- acquisition coverage, ingestion details, scheme evidence, rolling returns, and drawdown are under
  the top-level **Data** navigation item.

The next implementation sequence is:

1. Rerun the six data-API integration tests in an environment where Starlette/AnyIO worker threads
   are permitted; the remaining post-edit checks and production-diff review are complete as listed
   in **Verification state** below.
2. Add independently verified maximum-drawdown, volatility, and rolling-return consistency columns
   without changing the initial return-ranking semantics.
3. Add URL-backed screener filter state, sortable columns, and frontend interaction coverage.
4. Add per-fund benchmark-relative metrics only after implementing exact common-date alignment;
   the standalone reference deliberately does not present unmatched-endpoint excess return.
5. Add dataset-snapshot export only after the primary trailing-
   return ranking is independently verified and measured.

Existing IDCW identity blockers, broader lifecycle evidence, and provisional benchmark identities
remain explicit research-data limitations. They are not blockers for the initial Direct Growth
NAV-only screener and must not be “resolved” heuristically.

Alias-management closure state:

- an active alias must retain at least one member; audited deactivation releases every member for
  reassignment and records an immutable empty-member revision without changing AMFI source text;
- `classification_mapping_version` is rendered in the visible screener methodology note; and
- the remaining verification limitation is the six-test data-API integration file described below.

## Application startup

From the repository root, start both development servers in one terminal:

```bash
./scripts/start_app.sh
```

The launcher applies pending migrations and stops both processes together. The equivalent manual
commands, when separate terminals are preferred, are:

```bash
uv run --project backend alembic -c backend/alembic.ini upgrade head
uv run --project backend uvicorn mf_strategy_tester.api.main:app --reload
```

```bash
npm --prefix web run dev
```

Open `http://127.0.0.1:5173/`.

## Daily instrument synchronization

`scripts/sync_all_daily.sh` is the scheduler entrypoint for all currently supported instrument
series. It sequentially refreshes AMFI catalog/NAV, configured official Nifty price/GTR/NTR series,
and NSE/BSE ETF prices under `data/amfi-sync.lock`. The default correction window is seven calendar
days ending on today's `Asia/Kolkata` date. Every run retains per-lane JSON, an aggregate log, and a
final status file under `data/daily-sync-reports/`. Distribution and lifecycle acquisition remain
separate, lower-frequency workflows.

The wrapper passed Bash syntax validation and two isolated orchestration tests covering the exact
bounded arguments, aggregate status output, and continuation of independent benchmark lanes after
an AMFI failure. The complete backend unit suite now passes 235 tests; Ruff, strict mypy, frontend
tests/build, and `git diff --check` also pass. The real network workflow was not launched by the
agent because it is a long-running acquisition intended for a separate operator console.

## Latest acceptance artifacts

- Final targeted KFintech status:
  `data/rta-reports/kfintech-targeted-retry-20260901T001442Z.status`
- Final targeted KFintech import:
  `data/rta-reports/kfintech-targeted-import-20260901T001442Z.json`
- Final IDCW coverage: `data/rta-reports/coverage-20260901T001442Z.json`
- Final IDCW data quality: `data/rta-reports/data-quality-20260901T001442Z.json`
- Full KFintech breadth import: `data/rta-reports/kfintech-import-20260831T143846Z.json`
- Full KFintech breadth capture log: `data/rta-reports/kfintech-capture-20260831T143846Z.jsonl`
- CAMS capture log: `data/rta-reports/cams-capture-20260827T165432Z.jsonl`
- AdvisorKhoj publication: `data/advisorkhoj-reports/publication-20260824T123229Z.json`
- Lifecycle recovery status: `data/lifecycle-reports/sync-20260902T050429Z.status`
- Lifecycle recovery coverage: `data/lifecycle-reports/coverage-20260902T050429Z.json`
- Lifecycle alignment status: `data/lifecycle-reports/sync-20260902T052834Z.status`
- Lifecycle alignment coverage: `data/lifecycle-reports/coverage-20260902T052834Z.json`
- Benchmark coverage: `data/benchmark-reports/coverage-20260827T142937Z.json`

## Verification state

The targeted KFintech scheme-filter change passed 16 focused capture/parser tests, Ruff, and shell
syntax validation. At that checkpoint migration `20260831_0027` was applied, the targeted job
exited zero, and the normalization and coverage runs completed. That migration statement is
historical; the current applied head is `20260904_0032` as reported above.

The subsequent blocked-IDCW audit added the read-only `distribution-blocker-report` command. Its
live result reconciled all 67 options and 937 rows to the latest coverage snapshot. The identity
follow-up added the read-only `distribution-identity-backlog-report` and reconciled every unique
latest non-mapped CAMS, KFintech, and AdvisorKhoj review. All 199 backend unit tests passed; Ruff
formatting/lint and strict mypy passed for the changed Python files. Neither report wrote a
coverage run, source observation, mapping review, or canonical event.

The 2 September lifecycle audit read all 36 checksum-verified failed detail artifacts and confirmed
that each failure was solely an explicit null `Launch_Date`. The nullable-date implementation,
checkpoint-name idempotency fix, and wrapper hardening passed 208 unit tests plus four
source-ingestion integration tests, Ruff formatting/lint, strict mypy, shell syntax validation, and
migration `0027 -> 0028 -> 0027` on an empty SQLite database. The subsequent live recovery applied
`0028`, exited zero, and met all detail/launch coverage postconditions. No ingestion batch remains
running. The final alignment run exited zero, appended no issue, and left zero mismatches between
current checkpoint names and the latest immutable list snapshots.

The 2 September incremental NAV run independently completed all 57 fund houses through its
requested cutoff, and maintained statistics report 1 September as the newest valid NAV date. The
live database values in this file were rechecked after that run rather than copied from the older
handoff narrative.

The screener pivot removed the empty strategy catalog, API, domain/repository/service code, UI,
and tests. Migration `0029` passed fresh upgrade, downgrade reconstruction, and non-empty-catalog
refusal checks before it was applied locally. The additive classification-reference migration
`0030` passed fresh upgrade, downgrade, re-upgrade, and schema-drift checks. Before the local-alias
change, the suite passed 226 backend tests, Ruff, strict mypy, six frontend tests, TypeScript
compilation, and the production frontend build.

Migration `0031` subsequently passed an empty-database upgrade, downgrade to `0030`, and re-upgrade,
and it was applied to the retained local database. Direct database checks after application found
103 canonical classifications, 222 retained AMFI source labels, 25 screener aliases, 57 mapped
canonical classifications, and 25 immutable alias revisions. The Direct Growth one-year selector
returned 54 unique concise choices; Corporate Bond resolved across both retained AMFI families
with 26 candidates, 21 eligible options, and five explicit exclusions. A live service smoke check
returned the selected alias and its mapping version in the screener response. The backend unit
suite passed 222 tests. The data-API integration file passed five tests before the newest alias-
management API test was added. That was the pre-review verification checkpoint superseded by the
4 September results below.

The 4 September production-diff review added the explicit alias deactivation/release rule, rejected
blank normalized audit reasons, rendered the mapping version, and corrected the screener candidate
query to use latest observed metadata while retaining pre-inception options as explicit stale
exclusions. It also aligned the ORM alias-member index name with migration `0031`; `alembic check`
now reports no model drift. A fresh temporary database passed `0030 -> 0031 -> 0030 -> 0031` and
ended at head.

A read-only live smoke check of the corrected Corporate Bond one-year Direct Growth query retained
the expected 26 candidates, 21 eligible options, and five exclusions with mapping version
`screener-open-debt-corporate-bond:v1`; it completed in 3.888 seconds on the retained 33.7-million-
row NAV dataset.

All 227 backend unit tests and all four source-ingestion integration tests pass. Ruff formatting and
lint, strict mypy over 52 source files, `git diff --check`, all six frontend tests, TypeScript
compilation, and the production Vite build pass. The six data-API integration tests collect, but
cannot execute in the current managed sandbox: even a minimal synchronous FastAPI endpoint hangs in
AnyIO's worker-thread bridge, while an async endpoint succeeds. A bounded stack dump localizes the
block before application startup/query execution. This reproduces the older documented
Python 3.13.13 / AnyIO 4.14.2 `TestClient` environment limitation and is not evidence of an
application assertion failure. Those six tests, including the new HTTP deactivation/reassignment,
blank-reason, and pre-inception exclusion assertions, remain the final verification gate.

The 4 September ETF/index/benchmark screener extension subsequently passed 233 backend unit tests,
Ruff formatting/lint, strict mypy over 53 source files, seven frontend tests, TypeScript compilation,
the production Vite build, and `git diff --check`. A retained-database smoke check exposed 321
eligible Direct Growth index-fund options, 16 eligible Direct Growth ETF options, and 14 official
Nifty reference series for the one-year selector. The Nifty 50 TRI check correctly returned
`stale_endpoint` (24 August observation versus 2 September NAV as-of, nine calendar days) under the
seven-day tolerance instead of displaying an unmatched benchmark return.

Migration `0032` then backfilled only active canonical classifications without an existing local
mapping. Its populated migration fixture preserved an existing user alias, created deterministic
singleton aliases, disambiguated equal short labels with canonical family context, passed
upgrade/downgrade/re-upgrade, and reported no Alembic model drift. After local application the
database has 103 active canonical classifications, 103 alias memberships, 71 aliases, 71 immutable
revisions, and zero active classifications without an alias. The existing user-created Silver ETF
alias was preserved. The live alias-management response contains Balanced Hybrid Fund as singleton
alias `screener-singleton-56ebb05f69214d428b000da7fbf3adb2`; all 54 current Direct Growth one-year
dropdown choices resolve to IDs present in the alias-management response.

## Suggested first message in a new conversation

> Read `CLAUDE.md` and `docs/CURRENT_STATUS.md` completely. Preserve all ignored local research data.
> Accepted-source data acquisition is complete with documented limitations; do not rerun broad
> acquisition jobs. The product is now a customized mutual-fund performance screener. Migration
> `20260904_0032` and the local classification-alias management feature are implemented and applied
> to the retained database without changing AMFI source text. The production-diff review and all
> runnable checks are complete; rerun the six data-API integration tests in an environment that
> permits AnyIO worker threads. Then continue with the richer risk/consistency metrics in
> `docs/IMPLEMENTATION_PLAN.md`.
> The superseded strategy subsystem and empty local tables have already been removed.
