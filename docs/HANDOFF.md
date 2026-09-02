# Project handoff

**Snapshot date:** 2026-09-02 (`Asia/Kolkata`)

**Authoritative current checkpoint:** [`docs/CURRENT_STATUS.md`](CURRENT_STATUS.md). Read that file
first after clearing the conversation. The numeric status, pending-batch statements, and next-step
ordering below are a historical audit narrative through 24 August and are not the current operator
plan.

**Branch:** `main`

**Version-control state:** Phase 2 was committed as baseline `4fc7302`. Post-baseline lifecycle and
documentation changes may remain in the working tree; inspect before editing and do not reset,
clean, or discard them. The ignored local `data/` directory remains valuable research state.

## Historical handoff narrative

The remainder of this document is retained for detailed chronology and design rationale. Whenever
it conflicts with `docs/CURRENT_STATUS.md`, the current checkpoint controls.

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
- SQLite persistence, WAL mode, foreign-key enforcement, and twenty-three Alembic migrations through
  `20260821_0023`.
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
- Resumable AMFI distribution snapshot ingestion with per-scheme checkpoints, queryable immutable
  source rows, exact option-identifier reconciliation status, exact `Rate_of_div` source text, and
  explicit scalar and ratio source units.
- Opt-in distribution row quarantine with raw JSON, parser diagnostics, accepted/rejected counts,
  `completed_with_issues` status, and exact-signature retry resolution.

### Research data UI

The Data view provides:

- AMFI fund-house selection;
- stored scheme-option counts;
- exact AMFI classification filters;
- Direct/Regular and Growth/IDCW/Bonus filters;
- search by scheme name, AMFI scheme code, or ISIN;
- first/latest stored NAV dates, latest NAV, data-quality status, and coverage state;
- selectable scheme-option detail with NAV-only since-inception return/CAGR, maximum drawdown, and
  1/3/5/10-year rolling annualized-return distributions;
- point-to-point 3/5/10-year NAV CAGR cards beside since-inception NAV CAGR, using the same rolling
  endpoint alignment and missing-data policy;
- canonical IDCW record-date events with revision and exact AMFI artifact/parser provenance;
- explicit option-level payout coverage status that never treats an empty AMFI response as proof of
  no payouts;
- the latest ingestion-run summary and immutable source-capture ledger.

The browser uses metadata attached to each scheme option's latest stored NAV record. Financial
metrics are not calculated in the frontend.

## Local dataset snapshot

The local `data/` directory is intentionally ignored by Git and was approximately **27 GB** at
handoff. It remains available in this workspace but is not part of a fresh clone.

| Measure | Value |
| --- | ---: |
| AMFI fund-house catalog entries marked active | 57 |
| Stored AMFI scheme options | 33,243 |
| Valid current NAV observations | 33,632,499 |
| Quarantined/error current NAV observations | 342,630 |
| Earliest valid NAV date | 2006-04-01 |
| Latest valid NAV date | 2026-08-23 |
| Fully covered fund houses through requested cutoff | 57/57 |
| Distribution family checkpoints | 10,977/10,977 |
| Accepted rows in current distribution checkpoints | 36,965 |
| Immutable distribution source-row versions | 73,930 |
| Rejected distribution source rows in current checkpoints | 0 |
| Open distribution parse issues | 0 |
| Distribution source-row versions with unresolved option identifiers | 206 rows / 21 identifiers |
| Unmatched identifiers reviewed as AMFI source-only | 21/21 |
| Non-positive scalar distribution source-row versions | 450 rows / 114 identifiers |
| Positive, exact-ID, explicit dividend/IDCW amount source-row versions | 61,878 / 1,667 identifiers |
| Distribution source-row versions blocked by all normalization gates | 12,052 |
| Canonical distribution events/current revisions/all provenance links | 98,675 / 98,673 / 143,269 |
| Official HDFC notice rows / notice-backed events | 2 / 2 |
| AdvisorKhoj catalog histories / immutable source rows | 4,442 / 1,066,137 |
| AdvisorKhoj mapped / ambiguous / unresolved nonempty histories | 2,243 / 27 / 396 |
| AdvisorKhoj catalog histories returning no rows | 1,776 |
| IDCW coverage assessment | 2,423 events present / 1,315 blocked-only / 12,735 unverified-empty |
| Ingestion batches still marked running | 0 |

Freshness at handoff:

| Source area | Latest local acquisition/effective date | Handoff conclusion |
| --- | --- | --- |
| AMFI fund catalog | Acquired 19 Aug; official catalog rechecked 21 Aug | Still 57 entries |
| AMFI NAV | Acquired 24 Aug; latest valid observation 23 Aug | Current with newest date published in the acquired feed |
| AMFI scheme/distribution families | Refreshed 21 Aug | 10,977/10,977 current families completed; empty responses are not absence proof |
| AdvisorKhoj catalog/details | Acquired 20 Aug | Complete for that current third-party catalog |
| CAMS | 1,438 captures / 34,128 raw rows / 40,443 normalized source versions through 23 Aug | 1,144 mapped / 3 ambiguous / 291 unresolved; breadth incomplete; 1,004 retained failures |
| KFintech | 414 captures / 111,521 rows committed 21 Aug | 150 mapped / 18 ambiguous / 246 unresolved; full current 442-option universe attempted; 28 upstream postbacks failed |
| Official AMC notices | One HDFC notice dated 25 Feb | Optional supplemental provenance; broad documentary acquisition is not a completion gate |
| Scheme lifecycle | Collector implemented; long batch not run | Current AMFI launch facts pending acquisition; merger/closure evidence still absent |
| Benchmarks | One-day isolated `/tmp` smoke only | Research database has no index/ETF acquisition batch yet |

“Fully covered” means one committed `nav_sync_coverage` interval spans the complete requested
window from `2006-04-01` through `2026-08-24`. It means the official endpoint was queried; it does
not mean every scheme published a NAV on the cutoff date. A checkpoint at the end date is
insufficient if an earlier gap remains.

Full run `b800a34f-7fd1-48aa-a1b6-727046460f1d` established the historical base through 17 August.
Incremental run `6042d37d-790a-4a5a-8bf7-959b9b6f561a` completed all 57 fund houses through
21 August using parser `amfi-2026.08.8`. It observed 67,473 rows, inserted 17,652, classified
49,821 unchanged, created no financial revisions, and quarantined 891 non-positive NAV rows. The
newest valid source observation is dated 20 August. No ingestion batch remains running.

Incremental run `d91cf230-484b-4db7-95b1-9dffe4547a30` subsequently completed all 57 fund houses
through the requested cutoff of 24 August. It received 60,107 rows, inserted 9,943, classified
50,164 unchanged, created no financial revisions, and quarantined 781 non-positive NAV rows. The
newest valid source observation is dated 23 August. Maintained dataset statistics now report 33,243
scheme options, 33,632,499 valid current NAV rows, and 342,630 error rows.

### AMFI schema changes resolved 21 August

AMFI changed both official NAV contracts after the previous successful local sync. Parser
`amfi-2026.08.8` added header-specific field maps for both new contracts while retaining every
legacy contract needed to replay immutable historical artifacts. The daily and full scripts are
safe to run against these observed contracts; unknown headers and row widths still fail loudly.

The live 21 August `NAVAll.txt` response has this header:

```text
Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date
```

The live historical response has this header:

```text
Scheme Code;NAV Name;Plan;Option;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;Net Asset Value;Date
```

The current feed inspected on 21 August contained 14,288 scheme rows under the new schema: 8,299
were dated 20 August, 367 were dated 19 August, and the rest retained older scheme-specific last
publication dates. Successful current-feed batch `0f64c2bb-1865-4a94-93a8-09dd9330e666` retained
artifact SHA-256 `6d15531d6e263590ccdcb0f21a1786ac5301671bed7465b2fb69c444f7f2a533`.
The local latest valid NAV date is now 20 August.

Official endpoints used for reproduction:

- current NAV: `https://portal.amfiindia.com/spages/NAVAll.txt`;
- historical NAV: `https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx` with `mf`,
  `frmdt`, and `todt` query parameters; and
- fund catalog: `https://www.amfiindia.com/otherdata/scheme-details`.

The new `Plan` and `Option` values are carried on parsed rows and routed into normalized metadata
classification without treating names as identifiers. Blank qualifiers fall back independently to
the legacy name heuristic; unrecognized nonempty values classify as `unknown` rather than being
guessed. Deterministic fixtures cover both new and legacy formats, exact source-name preservation,
decimal/date handling, malformed widths, duplicates, and publication idempotency.

The distribution API independently added nullable `Plan` and `Option` fields. It also emits empty
strings for some historical option qualifiers. Parser `amfi-2026.08.10` introduced the exact
legacy or qualifier-aware object shapes, normalizes null/blank qualifiers to missing, and preserves
nonblank values. Migration `20260821_0018` makes those qualifiers queryable. Failed strict runs
`ba6010f9-1af9-4e5e-9850-20aafec784aa` and `0296f61a-af09-4d88-b800-7b5c99ef80eb` remain audit
evidence for the two discovered response cases.

Distribution ingestion encountered historical source forms that are not plain decimals. Parser
version `amfi-2026.08.10` preserves explicit `%` suffixes, represents the
independently verified `1:3` Taurus bonus ratio with separate numerator/denominator fields, and
separates the UTI source value `20% (Rs 2/- Per Unit` into percentage `20` and an annotated INR
amount per unit of `2`; observed `Per`/`per` casing variants are accepted. The complete source
survey found one additional dash-form value, `15%-Rs 1.50 Per Unit`. It was quarantined, supported
narrowly, and replayed through exact-signature resolution. Refresh run
`f7fe4100-e4f1-45d4-985a-4e8c78739998` completed all 10,977 current source families, accepted all
36,965 checkpoint rows, rejected none, and left no parse issue open. Of those families, 10,089
returned no rows; 36 of 57 active fund houses returned no distribution rows at all.

Generate the read-only Phase 2 acceptance report with:

```bash
uv run --project backend mfst data-quality-report
```

The report consolidates repeated NAV issue rows by issue code, scheme code, and NAV date while
retaining the raw occurrence count. It also lists exact AMFI distribution option identifiers that
do not match `scheme_options`; names are evidence for investigation, never automatic identity
keys. An abandoned ingestion batch can be closed only through the exact-ID, timezone-guarded
`reconcile-stale-batch` command after verifying that no worker owns it.

All 21 unmatched IDs have an append-only `source_only` review tied to the exact immutable AMFI
distribution artifact. None is mapped to a NAV option. The report now exposes 450 non-positive
scalar source-row versions across 114 identifiers; canonical distribution publication excludes
them until their semantics are established. Its read-only normalization gate classifies 61,878
source-row versions as narrow cash-amount candidates and blocks the remaining 12,052 by identifier,
value shape, unit, or explicit source-label evidence.

There are exactly two normalized source-row versions for each current AMFI row: the retained legacy
version and the qualifier-aware version from the refreshed contract. Normalization version
`amfi-distribution-2026.08.1` still has 31,219 stable, revisioned `idcw_cash` events. Refresh
normalization run `f748e113-310c-43e4-8cfe-ed8fafe645ac` inserted no events or revisions and
classified all 61,878 candidate source-row versions unchanged. AMFI's date remains explicitly
`record_date`; no payment date, ex-date, announcement availability, reinvestment behavior, or
investor cash flow was inferred.

Coverage assessment run `cb489b70-375d-482e-8414-19153f9b58d9` classified all 16,430 options whose
latest current NAV revision carries IDCW metadata, using append-only evidence snapshots. AdvisorKhoj
evidence was still evidence-only when this snapshot was produced. The result is 1,691
events-present, 1,910 blocked-source, and
12,829 unverified-empty. Of 10,977 completed AMFI family requests, 10,089 returned no rows; 36 of 57
active fund houses returned no distribution rows at all. Therefore,
distribution request completion is not payout-history completeness. See
`docs/DISTRIBUTION_COVERAGE.md` for status semantics and the HDFC Balanced Advantage source-gap
evidence.

Migration `20260817_0015` and parser `hdfc-amc-2026.08.1` add the first official-AMC notice path.
HDFC's February 2026 Balanced Advantage notice and scheme summary were captured with SHA-256
`d88b50dd619fbf276a7176671778ddaafc8587b7a052772fd766795eb3688362` and
`86a76bdb17a41424902bb45d184c5e1eda9b57f702086f36b19af189312d9f97`. Exact official mappings
published INR 0.250/unit on 2026-02-25 for regular code `100120` and direct code `118969`. A repeated
live publication inserted zero records/events/revisions and reported two unchanged rows.

Migration `20260817_0016` adds CAMS/KFintech RTA distribution ingestion. Captures preserve
proprietary RTA identifiers, source URLs, embedded-source checksums, Retail/Individual and
Corporate/Non-Individual values, optional KFintech ex/cum NAV, and capture timestamps. Automatic
AMFI mapping requires exact normalized scheme core, plan, and current NAV evidence. Canonical
revision links retain the exact mapping review; unresolved/ambiguous options and amount conflicts
are queryable and cannot mutate canonical payouts.

`scripts/sync_rta_distributions.sh` orchestrates resumable provider capture, partial import,
coverage, and data-quality reporting. `docs/RTA_DISTRIBUTIONS.md` contains commands, SQL, and source
limitations. Deterministic importer tests pass. The public RTA collectors still require full-volume
operational validation: CAMS is a browser-rendered Angular workflow with a visible 50-row limit, and
KFintech responses can be slow or expose new result shapes. Per-scheme failures continue to later
schemes and are retained in JSONL sidecars.

KFintech stops after five consecutive scheme failures by default; CAMS skips only the affected fund
and continues breadth traversal. RTA parser `rta-capture-2026.08.4` supports the observed live
headings, abbreviated-month dates, zero
Non-Individual values, and distinct same-date rows. Migration `20260821_0023` changes only the
secondary Non-Individual database constraint from positive to non-negative. Before network access,
the KFintech collector replays checksum-verified HTML retained in its immutable error sidecar. An
offline replay of the 408-line, 159 MB sidecar recovered 355 scheme captures containing 87,951
distribution rows: 351 nonempty histories and four explicit empty results. Eighteen latest retained
pages were genuine failed postbacks whose selected scheme was not retained. They are now deferred
by default—never classified as empty—so later fund houses can be acquired; an explicit environment
flag retries them after a source recovery. The live dataset retains 385 zero Non-Individual
observations and 80 capture/date groups with multiple distinct source rows.

The mapper now restricts each NAV-history query to exact IDCW name/plan candidates before applying
the unchanged exact date/Decimal NAV evidence gate. A read-only benchmark across all 355 captures
completed in about three seconds, issuing 208 indexed queries over 444,748 candidate NAV rows;
candidate sets contained at most four AMFI codes. `scripts/resume_rta_distribution_import.sh`
verifies the already committed capture/row signatures and resumes mapping/publication without RTA
network access or duplicate source-row ingestion. Resume run
`3a5b6197-ec31-492e-8352-a71ec3452f55` completed in about 35 seconds: 122 captures mapped, 18 were
ambiguous, 215 unresolved, and 33,535 canonical events/revisions were inserted. Two same-tier Axis
amount conflicts were deliberately left without a current revision rather than arbitrarily choosing
between proprietary KFintech scheme rows. The next breadth pass added 59 captures and 23,570 rows;
cumulative normalization run `57dcaea7-2efa-481c-96f4-f0362b68107f` classified 146 captures mapped,
18 ambiguous, and 250 unresolved, inserting another 16,423 events. The 28 remaining current options
all returned an unselected postback form.

The live RTA dataset is partial. Resumable CAMS breadth passes through 23 August brought the imported
corpus to 1,275 captures / 29,713 rows. That traversal loaded 24 of 26 fund selectors, retained
ASK and Unifi selector timeouts, and opened 13 fund-scoped scheme circuits without stopping the
complete traversal. Candidate-first mapping plus narrowly normalized CAMS decorations
(`Reinv`/`Exchange`, current names before `Formerly`/`erstwhile`, and explicit source abbreviations)
now classify 698 captures mapped, three ambiguous, and 737 unresolved. Mapping still requires an
exact plan where supplied and exact dated AMFI NAV evidence; the three multi-code matches remain
blocked. Fund-level failures and per-scheme
timeouts are retained and circuit-bounded rather than treated as empty observations.

The next breadth pass appended 83 captures / 2,535 rows, so the durable capture file contains
1,358 captures / 32,248 rows. Its first import stopped before creating a database batch because Franklin
weekly IDCW established meaningful ten-decimal source precision beyond the prior eight-decimal
corpus. Offline parser `.4` re-import `b2516f3c-80e6-49ec-b6aa-e239a8fd9b73` then inserted all 83
captures, 8,850 versioned source rows, 230 new events, and 2,168 revisions. Of those revisions, 1,938
were parser corrections to existing CAMS-backed events and 230 belonged to the new events. A second
reparse inserted nothing, proving idempotency.
KFintech's current landing page services only Axis (`128`) and BNP Paribas
(`178`); all 442 current IDCW/dividend options were attempted. It has 414 committed captures / 111,521
source rows and 50,016 canonical provenance links. The remaining 28 responses all returned the
unselected form rather than a result or explicit empty marker, so they remain auditable acquisition
failures. The AdvisorKhoj acquisition below is the approved broad secondary source for personal local
research, but it does not make RTA or official-AMC coverage complete.

Migration `20260820_0017` adds complete AdvisorKhoj catalog/capture persistence separately from the
official RTA model, and the local database is migrated to this head. The acquisition stores exact
catalog and detail responses through the content-addressed artifact infrastructure, validates a
one-to-one catalog/capture set, retains normalized observations and anomalies, and appends
plan/option/frequency plus multi-date AMFI NAV-fingerprint mapping reviews. It cannot insert
canonical distribution events.

The 20 August 2026 full acquisition captured all 4,442 histories discovered across 68 AMCs and
1,280 category queries. It imported 1,066,137 source rows and mapped 2,243 nonempty histories to
distinct AMFI codes; 27 nonempty histories remain ambiguous, 396 nonempty histories unresolved, and
1,776 catalog entries returned empty histories. Catalog SHA-256
`8d599fe8aa4d70bee3c3bcdff0e7e8bcc0e74c3b664f2a622aaa03c7e310c6a3` and capture SHA-256
`ecfc35a8125c9c5bba22a52f694f7021e8e0824210548582a5b679d170ae397b` are retained. No canonical
events were published. After KFintech publication and the latest CAMS acquisition/remapping, latest
combined coverage run `bb5883a6-244e-46b4-a4a5-a6e267c3294a` classified 16,430 IDCW options as
2,171 events-present, 1,541 blocked-source, and 12,718 unverified-empty. See
`docs/ADVISORKHOJ_DISTRIBUTIONS.md`.

A subsequent no-change CAMS attempt stopped after five new Bandhan scheme timeouts and demonstrated
that the original scheme circuit still blocked traversal of later fund houses. The collector now
scopes that circuit to the affected fund: it skips the rest of that fund after five consecutive
scheme failures and continues breadth acquisition. A separate five-consecutive-fund-selector
circuit remains the only source-driven early stop for the complete traversal.

The 23 August normalization reported 713 ICICI CAMS conflicts, all against primary AMFI amounts.
Audit showed 279 were DOM-rendered binary-float noise. The subsequent Franklin capture established
meaningful source precision through ten decimals; across all 64,496 CAMS retail/corporate values,
every value beyond ten decimals is within `1e-14` of its ten-decimal form. Parser `.4` preserves the
exact raw string but normalizes only that proven noise with a `1e-12` bound; unexplained precision
still fails loudly. Corrected source-row versions were appended without overwriting prior rows.
The conflict count fell exactly from 713 to the 434 genuine AMFI/CAMS differences, which remain
blocked under source precedence. CAMS now contributes 20,175 canonical provenance links.

The next normal breadth pass added another 80 captures / 1,880 rows but only one capture mapped.
This exposed an input-freshness dependency rather than a mapper defect: local AMFI NAV ends on
20 August, while 494 currently unresolved CAMS captures already have exact name/plan candidates and
NAV evidence dated after that cutoff. Those captures contain 12,343 rows. Run the daily AMFI NAV
increment first, then replay CAMS mapping/publication offline before spending more browser time.

That freshness dependency is now resolved. After the 24 August AMFI increment, offline CAMS replay
run `1916eaaa-0ed7-4989-bb77-3cc39973c735` mapped 1,144 of 1,438 captures, leaving three ambiguous
and 291 unresolved. It inserted 7,440 events/revisions from already committed rows. Coverage run
`2875e159-78da-4b30-96bd-5d78c41d2290` assessed 16,473 current IDCW options as 2,419
events-present, 1,317 blocked-source, and 12,737 unverified-empty. The larger denominator reflects
43 newly classified current IDCW options rather than a coverage regression.

All 664 amount conflicts in that replay were lower-priority CAMS values against canonical AMFI:
434 pre-existing ICICI differences and 230 newly mapped Franklin differences. No same-tier RTA
conflict occurred, and source precedence prevented every lower-priority value from replacing AMFI.
The canonical ledger now contains 98,541 events, 98,539 current revisions, 100,479 revisions in
total, and 143,132 source links (61,878 AMFI, 81,252 RTA, and two official-AMC links).

Offline KFintech replay `c3e94ab0-58ea-480d-a8a2-94a714113b39` against the same refreshed AMFI NAV
mapped four additional captures and inserted 134 events/revisions, leaving 150 mapped, 18 ambiguous,
and 246 unresolved. Its two same-tier Axis conflicts remain blocked. Coverage run
`c782abf0-c52b-4b5d-826f-4f71937e0696` is the pre-AdvisorKhoj baseline: 2,423 events-present,
1,315 blocked-source, and 12,735 unverified-empty among 16,473 current IDCW options. The canonical
ledger now has 98,675 events, 98,673 current revisions, 100,613 revisions in total, and 143,269
source links.

Within that current IDCW universe, 1,602 covered options are AMFI-only, 61 have both AMFI and CAMS
evidence, two are official-AMC-only, 610 are CAMS-only, and 148 are KFintech-only. Thus primary or
official evidence covers 1,665 options and RTA-only evidence adds 758. The acquired AdvisorKhoj
dataset has 246,968 mapped positive rows for 993 already-covered options and 514,487 rows for 1,245
blocked options, but none of the 12,735 unverified-empty options. Those 1,245 blocked options
contain 514,487 unique, internally consistent candidate event dates with no same-date amount
disagreement. This is an
upper bound on the immediate option-level fallback gain; the tertiary publisher must still apply
the recorded precedence and conflict rules.

The current AMFI catalog contains four new fund-house IDs with no scheme families: Lakshya (`88`),
Monarch (`89`), Nuvama (`90`), and Carnelian (`91`). Their official `populate-scheme` responses were
rechecked on 20 August and were empty arrays. Preserve that distinction: they are known catalog
entries with no upstream schemes, not failed acquisitions.

## Data-acquisition completion decision

The user explicitly made data acquisition the current gate before Phase 3. Complete and verify the
IDCW precedence batches, then lifecycle, then benchmarks. AdvisorKhoj may publish canonical
fallback events only through migration `20260821_0019`'s audited AMFI > CAMS/KFintech >
AdvisorKhoj precedence rule; it must not overwrite higher-priority evidence.

“Complete” does not mean inventing unavailable evidence. Residual unverified IDCW options,
unresolved merger/closure history, pre-2006 NAV absence, and provisional historical BSE ETF
identity must remain explicit in acceptance reports and future dataset manifests.

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

First complete the AMFI parser task in the critical-schema-change section above. The commands below
are intentionally documented for use **after** the new-format regression tests pass.

Run the complete historical synchronization in the foreground:

```bash
./scripts/sync_amfi_full.sh
```

The script runs migrations, acquires `data/amfi-sync.lock`, and requests only uncovered windows.
It defaults to today's date in `Asia/Kolkata`. To use an explicit reproducible cutoff:

```bash
MFST_SYNC_END_DATE=2026-08-21 ./scripts/sync_amfi_full.sh
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
bash -n scripts/sync_amfi_full.sh scripts/sync_amfi_daily.sh scripts/sync_amfi_distributions.sh \
  scripts/sync_rta_distributions.sh scripts/resume_rta_distribution_import.sh \
  scripts/sync_advisorkhoj_distributions.sh
```

Latest verification result:

- 148 backend unit tests passed after adding the Phase 2 data-quality report, stale-batch guard,
  distribution identity-review ledger, canonical distribution publication, RTA parsing/mapping,
  Advisorkhoj secondary-source parsing/mapping, conflict preservation, provenance, and coverage
  accounting, lifecycle and benchmark acquisition, and the new AMFI NAV/distribution contracts.
  The existing FastAPI integration
  `TestClient` tests still hang during fixture startup with the installed Python 3.13.13 / AnyIO
  4.14.2 runtime; a bounded 180-second full-suite attempt reproduced the hang without a failing
  assertion.
- Ruff lint and formatting checks passed.
- mypy strict checks passed.
- Seven frontend tests and the TypeScript/Vite production build passed.
- Live fund-house query: approximately 0.07 seconds on the handoff database.
- Live filtered scheme page: approximately 0.11 seconds on the handoff database.
- Live NAV performance calculation: approximately 0.20–0.29 seconds for 3,290–6,831 observations.
- Latest-50 canonical distribution drill-down: approximately 0.02 seconds for an option with 1,522
  events.
- Fresh migration from an empty SQLite database reached `20260821_0023`, and `alembic check`
  reported no model drift. The research database is also at `0023`; no lifecycle or benchmark
  acquisition batch has been run against it.
- The read-only acceptance report completed against the refreshed dataset; the latest coverage
  assessment examined 16,473 IDCW options.

## Phase status and next-session order

Phase 0 and Phase 1 are implemented. Data acquisition is again the active priority. Migration
`20260821_0019` and the tertiary fallback publisher implement the explicit AMFI > CAMS/KFintech >
AdvisorKhoj rule without destructive overwrites. The local long-running AdvisorKhoj publication
batch has not yet been executed.

Migration `20260821_0020` and `scripts/sync_scheme_lifecycle.sh` provide the current AMFI
family/detail/launch lane. Migrations `20260821_0021`-`0022` and the benchmark scripts provide
official Nifty price/TRI/NTR plus NSE/BSE ETF acquisition. Only bounded `/tmp` benchmark smoke
tests have run: Nifty inserted three observations for each of price/GTR/NTR across 01-03 Jan 2020;
BSE inserted 244 prices on 20 Aug 2026 and added six BSE-only instruments beyond the NSE master.
The BSE observations were correctly labelled provisional because the classifier snapshot was dated
21 Aug. Apparent long wall times around these probes were user approval waits, not source latency.

Next session should proceed in this order:

1. Complete IDCW option coverage in precedence order: AMFI, CAMS/KFintech, AdvisorKhoj. AMFI is
   current through the 24 August requested cutoff and CAMS has been replayed against it. Next replay
   KFintech offline with
   `scripts/resume_rta_distribution_import.sh data/rta-captures/kfintech-full.jsonl`, then continue
   the resumable CAMS breadth acquisition; retained failures are deferred until all fund houses
   have been traversed. Run `scripts/publish_advisorkhoj_fallback.sh` only against the final gaps.
2. Acquire and reconcile scheme lifecycle events without treating current names or metadata as
   historical truth. The resumable AMFI family/detail collector is implemented in
   `scripts/sync_scheme_lifecycle.sh` but its long local batch is not yet run.
3. Acquire versioned benchmark index series and NSE/BSE ETF prices with immutable raw artifacts and
   explicit price-return/total-return semantics. The implementation and operator scripts now exist;
   run and verify `scripts/sync_nifty_benchmarks.sh` and `scripts/sync_etf_prices.sh`.
4. Define the immutable dataset-snapshot manifest only after these acquisition lanes have acceptance
   reports.

## Known limitations and risks

- The observed AMFI NAV and distribution contract changes are supported, but future unknown headers,
  row widths, or JSON object shapes deliberately stop ingestion for review.
- The 57 AMFI catalog entries are provider/fund-house identifiers, not 57 schemes. AMFI catalog
  presence alone does not prove that every provider is economically active throughout history.
- Requested NAV-window coverage is complete for all 57 currently catalogued fund houses, but the
  current scheme-list universe is not evidence of a survivorship-free historical universe.
- Official historical NAV endpoint probing returned no observations before 1 April 2006; earlier
  histories for older schemes are not present locally.
- The all-family AMFI lifecycle collector is implemented but its long batch is not run. AMFI
  current details do not by themselves establish mergers, closures, predecessor identities, or
  historical eligibility.
- AMFI classification strings contain historical formatting variants and are retained faithfully;
  canonical category mapping is not implemented.
- Canonical IDCW record-date events exist, but payment dates, announcement availability, and
  reinvestment/investor cash flows do not.
- AMFI distribution coverage is materially incomplete. Under the accepted local-research policy,
  AMFI, CAMS/KFintech, and AdvisorKhoj exhaust the required sources; `unverified_empty` remains an
  explicit absence-of-evidence state and must never be presented as proof of no payout.
- CAMS coverage is partial, and KFintech mapping/publication is complete only for the two currently
  captured fund houses; remaining RTA network gaps must be exhausted or retained as explicit
  failures. Only one HDFC official notice format has been published, and expanding that documentary
  source is optional. AdvisorKhoj is accepted tertiary evidence under the configured precedence.
- Complete scheme lifecycle and merger resolution is not implemented.
- Point-in-time information-availability timestamps remain an explicit future engine concern; a
  NAV date alone does not prove when the NAV was knowable.
- Benchmark acquisition is implemented but its research-database history is not acquired. BSE has
  no accepted security-level historical ETF roster; later official mappings are labelled
  provisional, and the smaller official current roster schema raises `partial_bse_roster`.
- The portfolio accounting engine, backtest orchestration, benchmark-relative metrics, run ledger,
  and research-result UI are not implemented.
- Local source artifacts and `research.db` are intentionally absent from Git. Back up `data/`
  separately before destructive storage or migration work.
- The working tree has 109 modified/untracked paths. Do not use destructive Git cleanup commands;
  review and preserve all existing work.

## Important files

- `AGENTS.md` — mandatory correctness and working rules.
- `docs/CURRENT_STATUS.md` — authoritative conversation-reset checkpoint and current operator plan.
- `README.md` — local setup and repository overview.
- `docs/IMPLEMENTATION_PLAN.md` — phase plan and exit criteria.
- `docs/ARCHITECTURE.md` — accepted architecture decisions and invariants.
- `docs/AMFI_SYNC.md` — ingestion conventions and scheduler usage.
- `docs/DISTRIBUTION_IDENTITY_REVIEW.md` — reviewed distribution identity exceptions and evidence
  rules.
- `docs/DISTRIBUTION_COVERAGE.md` — append-only coverage semantics and missing-source evidence.
- `docs/NAV_PERFORMANCE.md` — NAV-only return, rolling-window, and drawdown conventions.
- `backend/src/mf_strategy_tester/services/nav_sync.py` — resumable synchronization behavior.
- `backend/src/mf_strategy_tester/services/nav_publication.py` — normalized NAV publication.
- `backend/src/mf_strategy_tester/services/distribution_sync.py` — resumable distribution source
  snapshots and exact-row publication.
- `backend/src/mf_strategy_tester/api/routes/data.py` — coverage and scheme-browser queries.
- `web/src/components/DataWorkspace.tsx` — research-data browser.
- `scripts/sync_amfi_full.sh` — complete historical resume entrypoint.
- `scripts/sync_amfi_daily.sh` — incremental scheduled entrypoint.
- `scripts/sync_amfi_distributions.sh` — complete distribution source resume/refresh entrypoint.
- `scripts/sync_rta_distributions.sh` — CAMS/KFintech IDCW source capture/import entrypoint.
- `scripts/resume_rta_distribution_import.sh` — no-network recovery for committed RTA rows whose
  mapping/publication stage was interrupted.
- `scripts/sync_advisorkhoj_distributions.sh` — complete AdvisorKhoj catalog/capture/import entrypoint.
- `scripts/publish_advisorkhoj_fallback.sh` — resumable tertiary fallback publication and acceptance
  reports for the already imported AdvisorKhoj dataset.
- `scripts/sync_scheme_lifecycle.sh` — resumable AMFI family catalog/detail/launch acquisition and
  lifecycle completeness reports.
- `docs/BENCHMARK_ACQUISITION.md` — official source contracts, return-series semantics, commands,
  and BSE point-in-time limitations.
- `scripts/sync_nifty_benchmarks.sh` — resumable official Nifty price/TRI/NTR history.
- `scripts/sync_etf_prices.sh` — resumable NSE/BSE ETF classification and exchange prices.
