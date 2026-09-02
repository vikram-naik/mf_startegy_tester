# Current project status

**Authoritative conversation-reset checkpoint:** 2026-09-01 (`Asia/Kolkata`)
**Branch:** `main`
**Database migration:** `20260831_0027` (head)

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
- The working tree is intentionally dirty with more than 100 modified/untracked paths, and `data/`
  is about 40 GB and ignored by Git. Preserve all existing work; do not reset, clean, or delete it.

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
| Scheme options | 33,251 |
| Valid current NAV revisions | 33,646,811 |
| Error/quarantined current NAV revisions | 342,756 |
| Valid NAV date range | 2006-04-01 through 2026-08-27 |
| Alembic head | `20260831_0027` |

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

The full run `9c86a616-669c-40c1-9177-2b2845d4862b` completed with issues:

- 57/57 fund scheme-list snapshots completed;
- 10,945/10,981 family detail rows and launch events acquired;
- 36 scheme-detail requests failed and remain explicit gaps;
- no conflicting launch dates were detected.

This captures launch evidence only. Merger, closure, predecessor identity, and survivorship-free
historical-universe evidence remain incomplete.

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
3. Investigate the 36 lifecycle detail gaps and build explicit merger/closure/predecessor evidence.
4. Reduce benchmark identity uncertainty, especially provisional historical BSE observations,
   without applying current roster identity retrospectively.
5. Define an immutable dataset-snapshot manifest tying NAV, IDCW, lifecycle, benchmark, parser, and
   code versions together.
6. Then implement Phase 3 deterministic portfolio accounting and backtests, beginning with Growth
   NAV and preventing IDCW or expense double counting.

The identity backlog is now ranked and fully explained by the retained evidence; it has no safe
local-only mapping candidate. The next actionable task is item 3: inspect the 36 lifecycle detail
gaps and use any official predecessor/merger evidence to improve both lifecycle integrity and later
identity review. Revisit item 1 only when a stable identifier or separate official continuity
artifact is available.

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
- Lifecycle coverage: `data/lifecycle-reports/coverage-20260824T125955Z.json`
- Benchmark coverage: `data/benchmark-reports/coverage-20260827T142937Z.json`

## Verification state

The targeted KFintech scheme-filter change passed 16 focused capture/parser tests, Ruff, and shell
syntax validation. Migration `20260831_0027` is applied, the latest targeted job exited zero, the
latest normalization and coverage runs completed, and no ingestion batch is running. Do not infer
that the entire dirty working tree was revalidated by those focused checks; rerun proportionate
tests before declaring later implementation work complete.

The subsequent blocked-IDCW audit added the read-only `distribution-blocker-report` command. Its
live result reconciled all 67 options and 937 rows to the latest coverage snapshot. The identity
follow-up added the read-only `distribution-identity-backlog-report` and reconciled every unique
latest non-mapped CAMS, KFintech, and AdvisorKhoj review. All 199 backend unit tests passed; Ruff
formatting/lint and strict mypy passed for the changed Python files. Neither report wrote a
coverage run, source observation, mapping review, or canonical event.

## Suggested first message in a new conversation

> Read `AGENTS.md` and `docs/CURRENT_STATUS.md` completely. Treat the dirty working tree and all
> local data as existing user work. Accepted-source data acquisition is complete with documented
> limitations; do not rerun long acquisition jobs. The retained RTA/AdvisorKhoj identity backlog is
> ranked and contains no safe local-only mapping. Continue with the 36 lifecycle detail gaps and
> official predecessor/merger evidence before defining the immutable dataset snapshot.
