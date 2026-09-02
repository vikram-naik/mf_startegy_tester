# Current project status

**Authoritative conversation-reset checkpoint:** 2026-09-02 (`Asia/Kolkata`)
**Branch:** `main`
**Repository migration head:** `20260902_0028`
**Local dataset migration applied:** `20260902_0028`

Read `AGENTS.md` before changing code. This file supersedes older numeric status and next-step
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
  tests and read-only diagnostics only. See `AGENTS.md` section 39.
- Phase 2 was committed as baseline `4fc7302`. The ignored `data/` directory is about 40 GB and
  remains valuable local research data; do not reset, clean, or delete it.

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
| Locally applied Alembic revision | `20260902_0028` |

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

## Work remaining after acquisition

The next work is data-quality and identity resolution, followed by the reproducible dataset
snapshot and Phase 3 accounting/backtesting—not another blind acquisition pass.

1. Resolve high-value RTA and AdvisorKhoj identities only where stable AMFI codes, ISINs, official
   predecessor/merger evidence, or the existing exact evidence gates support the mapping. Use
   `mfst distribution-identity-backlog-report` to rank later official-evidence work; do not weaken
   the current gates merely to reduce the backlog.
2. Retain the audited 67 blocked IDCW options unless new official evidence resolves their
   percentage, Bonus/Dividend, or non-positive semantics; use
   `mfst distribution-blocker-report` after later coverage snapshots to detect changes.
3. Pursue separate official merger/closure/predecessor artifacts without name-based mapping; the
   null-launch recovery and checkpoint-name alignment are complete.
4. Reduce benchmark identity uncertainty, especially provisional historical BSE observations,
   without applying current roster identity retrospectively.
5. Define an immutable dataset-snapshot manifest tying NAV, IDCW, lifecycle, benchmark, parser, and
   code versions together.
6. Then implement Phase 3 deterministic portfolio accounting and backtests, beginning with Growth
   NAV and preventing IDCW or expense double counting.

The identity backlog is now ranked and fully explained by the retained evidence; it has no safe
local-only mapping candidate. No lifecycle operator action remains. The next engineering task is
explicit official predecessor/merger evidence or, if none is available for a selected high-value
identity, item 5's immutable dataset-snapshot manifest. Revisit item 1 only when a stable identifier
or separate official continuity artifact is available.

## Application startup

From the repository root, use separate terminals:

```bash
uv run --project backend uvicorn mf_strategy_tester.api.main:app --reload
```

```bash
npm --prefix web run dev
```

Open `http://127.0.0.1:5173/?view=data`.

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
historical; the current applied head is `20260902_0028` as reported above.

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

## Suggested first message in a new conversation

> Read `AGENTS.md` and `docs/CURRENT_STATUS.md` completely. Preserve all ignored local research data.
> Accepted-source data acquisition is complete with documented limitations; do not rerun broad
> acquisition jobs. Lifecycle null-launch recovery and checkpoint alignment are complete. Continue
> with explicit official predecessor/merger evidence or the immutable dataset snapshot.
