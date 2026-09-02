# Phase-wise implementation plan

## Product outcome

After source data is ingested, a user can build and version a strategy in the browser, select or construct portfolios, run it over a historical period, compare it with an explicit benchmark, and inspect every decision, transaction, assumption, warning, and source input. Routine strategy experimentation requires no code change.

## Phase 0 — engineering foundation (bootstrapped)

**Deliverables**

- Python and TypeScript project structure, local configuration, linting, typing, and tests.
- FastAPI service, SQLite repository, migration framework, and health endpoint.
- Immutable versioned strategy catalog with a validated declarative schema.
- Initial visual builder for category, signal, selection count, allocation, schedule, dates, capital, execution lag, and benchmark.
- Architecture decisions and explicit financial invariants.

**Exit criteria**

- A strategy can be created and revised through the API; revisions cannot mutate history.
- Backend tests, migration upgrade, lint/type checks, and frontend tests/build pass.

## Phase 1 — source capture and data provenance (implemented)

**Deliverables**

- AMFI adapters for scheme master data, `NAVAll.txt`, historical NAV, and IDCW/distribution history.
- Content-addressed immutable raw artifact store.
- Ingestion batches recording source URL/identifier, checksum, retrieval time, parser version, counts, and failures.
- Strict parsers with representative fixtures and structural-change failures; distribution source
  surveys may explicitly quarantine individual bad rows, but never structural envelope failures.
- CLI commands for independent full/incremental ingestion; no ingestion coupled to page requests.

**Exit criteria**

- Re-downloading identical content is idempotent.
- Malformed source structure fails loudly without partially publishing normalized data.
- Every accepted normalized value is traceable to a raw artifact and batch.

Source capture and provenance are complete. Phase 2 publication retains a source mapping for every
normalized NAV observation, including repeated and revised AMFI publications.

## Phase 2 — canonical research model and data quality

**Current status:** AMFI fund discovery, complete/resumable historical NAV ingestion, immutable NAV
revisions, option-level metadata versions, source lineage, zero-NAV quarantine, interval coverage,
incremental overlap, scheduler entrypoint, live UI coverage, resumable distribution source
snapshots, auditable opt-in distribution row quarantine, conservative distribution candidate
gating, and provenance-linked revisioned `DistributionEvent` publication are implemented without
publishing portfolio cash flows. The research UI exposes NAV-only since-inception/rolling returns,
drawdown, canonical distribution provenance, and append-only option-level distribution evidence
coverage per scheme option. The first coverage snapshot proves that the AMFI distribution endpoint
is materially incomplete. A first HDFC official-notice adapter now captures the declaration and a
separate scheme-summary identity artifact, publishes exact-code multi-source provenance, and blocks
amount conflicts. Broader historical HDFC and multi-AMC documentary coverage is optional under the
accepted local-research source policy; AMFI, CAMS/KFintech, and AdvisorKhoj are the acquisition
completion sources. Their acquisition runs are complete with retained source limitations, and the
latest coverage snapshot contains 875,315 canonical events across 3,932 event-present options. The
AMFI family/detail/launch lifecycle batch has run for all 57 fund catalogs: 10,945 of 10,981
families have detail-backed launch events. The 36 prior detail gaps were audited as valid official
rows with explicit null launch dates; the parser and model retain them as unknown-date details, and
the targeted local recovery completed without inventing launch events. Official Nifty price/TRI/NTR
and NSE/BSE ETF batches have also run; their acceptance report retains provisional BSE identity,
partial-roster, empty-period, and invalid-row issues rather than repairing them heuristically. The
21 option-level identifiers covering 208 source rows that are absent from NAV history now have
append-only `source_only` reviews referencing their AMFI artifacts, but none has evidence for a NAV
mapping. In addition, 440 non-positive scalar source-row versions across 109 identifiers must
remain outside canonical cash flows, and non-zero values change unit convention on 06-Apr-2009.

The distribution data-acquisition implementation has an operational precedence rule. The complete
current AdvisorKhoj catalog (4,442 histories / 1,066,137 rows) is stored immutably with 2,243
evidence-backed AMFI mappings and an explicit ambiguous/unresolved backlog. AdvisorKhoj can publish
only as the tertiary fallback after AMFI and CAMS/KFintech evidence is evaluated. The required
operator batches and acceptance reports are complete as of 2 September 2026; remaining RTA/Advisor
identities, source conflicts, and unverified-empty options are explicit data-quality work rather
than a reason to infer events or rerun unchanged full acquisitions. Current counts and exact
reports are in `docs/CURRENT_STATUS.md`.
Core accounting should begin with Growth-option NAV and canonical events only, while IDCW cash-flow
support remains gated on explicit source trust and double-counting tests.

**Deliverables**

- Explicit AMC, scheme, plan, option, stable identifier, NAV observation, distribution event, and lifecycle models.
- Identity resolution centered on AMFI scheme codes and ISINs, including rename/merge evidence.
- Point-in-time metadata/lifecycle history to avoid current-universe survivorship assumptions.
- Data-quality rules for duplicates, invalid values/dates, stale feeds, gaps, extreme moves, identifier conflicts, and option/distribution inconsistencies.
- Research APIs and UI screens for scheme search, NAV/distribution inspection, provenance, and quality issues.

**Exit criteria**

- Natural duplicates are blocked by database constraints.
- Missing observations are classified rather than zero-filled or silently forward-filled.
- Users can drill from a displayed observation to its source artifact.

## Phase 3 — deterministic portfolio accounting engine

**Deliverables**

- Valuation-calendar and information-availability abstractions.
- Explicit signal date, decision time, applicable NAV date, debit date, and unit-allocation convention.
- Decimal-safe order, transaction, lot/unit, cash, contribution, withdrawal, fee, and IDCW ledgers.
- Growth and IDCW treatment without distribution or expense double counting.
- Hand-audited golden scenarios for lump sum, SIP, rebalance, missing NAV, holiday, redemption, and distribution flows.

**Exit criteria**

- Golden fixtures independently reconcile units, cash, transactions, and terminal value.
- The engine performs no network access and is deterministic for strategy revision plus dataset snapshot.
- Look-ahead checks reject information unavailable at a decision timestamp.

## Phase 4 — no-code strategy composition

**Deliverables**

- UI steps for universe, eligibility, signals, ranking/filtering, allocation, risk constraints, rebalance, execution, cash flows, costs, and benchmark.
- Registry of typed engine primitives with machine-readable parameter metadata, enabling UI controls to be rendered from capabilities.
- Composable boolean/arithmetic expression graph for filters and scores; no arbitrary Python or JavaScript execution.
- Templates for lump sum, SIP, fixed portfolio, top-N momentum, risk-weighted, and multi-signal strategies.
- Validation preview showing eligible historical coverage and contradictions before a run.

**Exit criteria**

- All shipped primitives and their combinations are configurable without code changes.
- Documents are schema-versioned, migratable, diffable, and reproducible.
- Unsafe, circular, incompatible, or point-in-time-invalid graphs are rejected before execution.

## Phase 5 — backtest orchestration and reproducible runs

**Deliverables**

- Persisted run configuration referencing strategy revision, dataset snapshot, application version, date range, timing, costs, and benchmark.
- Local job runner with progress, cancellation boundaries, structured logs, and actionable failures.
- Persisted transaction ledger, daily portfolio state, allocation history, warnings, and run manifest.
- Run comparison and exact rerun capability.

**Exit criteria**

- The same versioned inputs produce identical outputs within documented numeric tolerances.
- Partial/failed runs are never presented as completed results.
- A completed result can be audited from metric to portfolio state to normalized row to raw artifact.

## Phase 6 — metrics, benchmarks, and research visualization

**Deliverables**

- Independently tested total return, CAGR, XIRR, volatility, drawdown/depth/duration, rolling returns, and benchmark-relative metrics.
- Explicit frequency, annualization factor, day count, risk-free rate, date alignment, and total-return/price-return labels.
- Equity/drawdown charts, benchmark comparison, rolling windows, allocation view, transaction ledger, cash-flow view, and metric definitions.
- Export of configuration, run manifest, ledgers, time series, warnings, and metrics.

**Exit criteria**

- Metrics are hidden or marked invalid when prerequisites are not met.
- Every formula has an independently calculable test fixture.
- Charts and tables expose units, dates, assumptions, loading/empty/error states, and provenance links.

## Phase 7 — hardening and operational readiness

**Deliverables**

- Incremental-ingestion recovery, source revision workflow, database backup/restore, migration tests, and dataset rebuild tooling.
- Performance measurements for historical ingestion, time-series queries, rolling calculations, and repeated runs.
- Dependency/security review, localhost-only defaults, input/file limits, and structured observability.
- High-value browser tests for ingest status, strategy creation, run execution, audit drill-down, and export.

**Exit criteria**

- Backup and restore are exercised, migrations run from an empty database and the previous release, and recovery procedures are documented.
- Target datasets and research views meet measured local performance budgets.
- No known high-severity correctness, provenance, security, or reproducibility defect remains.

## Main risks

1. **Historical identity and survivorship:** AMFI history may not fully express lifecycle events. Preserve evidence, show coverage limitations, and never imply survivorship-free results without proof.
2. **Information availability:** NAV dates do not alone prove when values became knowable. Timing conventions must remain explicit and configurable.
3. **Source format drift:** strict parsing and raw retention are mandatory; permissive parsing could silently corrupt research.
4. **No-code scope creep:** arbitrary user formulas can undermine point-in-time safety. Use a typed capability registry and validated expression graph, not code evaluation.
5. **Metric credibility:** visually plausible results can still be wrong. Golden portfolio ledgers and independent formula fixtures gate result presentation.
