# Mutual-fund screener implementation plan

## Product outcome

Build a local-first mutual-fund screener that ranks comparable Indian mutual-fund scheme options
across fund houses using the normalized, provenance-linked NAV dataset. The primary workflow is to
answer:

> Which funds performed best over a declared period, within the fund houses, classifications, plan,
> and option types I selected?

The product is a research screener, not a strategy builder, portfolio constructor, recommendation
engine, or backtester. Every ranking must expose its financial assumptions, exact NAV endpoints,
data freshness, exclusions, and stable AMFI scheme code.

## Ranking correctness boundaries

- The comparison unit is an AMFI scheme option identified by AMFI scheme code. Scheme names are
  descriptive attributes and must not be used as durable identifiers.
- The first ranking release is limited to Growth options. NAV-only returns are economically
  incomplete for IDCW options because distributions are excluded; IDCW must not be mixed into a
  Growth ranking.
- Direct and Regular plans are separate options. The default universe is Direct Growth, and any
  Regular-plan view must be explicitly selected and labeled.
- “Best performing” means trailing NAV return over the selected horizon—not a recommendation and
  not a prediction of future performance.
- The default screen is all fund houses, Direct Growth, one-year trailing return, as of the latest
  valid dataset NAV date. Every default remains visible and user-adjustable.
- Horizons shorter than one year use absolute return. Horizons of one year or longer use CAGR with
  actual elapsed days and an `actual/365` convention. Both raw total return and annualized return
  remain available in the response.
- The requested `as_of` date defaults to the latest valid dataset NAV date. Each ranked option uses
  its latest valid NAV on or before that date and is excluded if the endpoint exceeds the declared
  staleness tolerance.
- The start endpoint is the first valid NAV on or after the calendar target date within a declared
  tolerance. Actual start/end dates and elapsed days are returned for every row.
- Missing NAVs are never zero-filled or silently forward-filled. Quarantined NAV revisions are
  excluded.
- Initial rankings use the latest observed scheme metadata and therefore are not survivorship-free
  historical-universe studies. The UI must display this limitation.

## Existing data foundation to retain

The following capabilities are complete foundations for the screener and remain in scope:

- immutable AMFI raw artifacts and audited ingestion batches;
- complete/resumable historical and incremental NAV acquisition;
- AMFI-code-based scheme-option identity and versioned metadata;
- immutable NAV revisions, provenance links, coverage intervals, and data-quality quarantine;
- current scheme browser, single-option NAV performance, drawdown, IDCW evidence, and source drill-
  down;
- accepted-source IDCW, lifecycle, and benchmark datasets with their documented limitations; and
- local FastAPI, SQLite, React, TypeScript, migration, test, and observability foundations.

Current dataset counts and acquisition acceptance artifacts remain authoritative in
`docs/CURRENT_STATUS.md`.

## Phase 0 — remove the superseded strategy product (implemented)

**Deliverables**

- Remove the strategy builder, saved-strategy library, strategy API routes and schemas, declarative
  strategy domain model, repository/service code, and their tests.
- Remove strategy/backtest navigation, product copy, architecture decisions, and roadmap content.
- Replace product-facing “MF Strategy Tester/Lab” branding with “MF Fund Screener”.
- Preserve the original Alembic migration chain for reproducibility; add a forward migration that
  drops the unused strategy tables. The pre-migration local audit found zero `strategies` and zero
  `strategy_versions` rows, so the applied migration discarded no saved user records.
- Keep ingestion, normalized research data, existing analytics, and ignored local data intact.
- Do not rename the internal Python package in this phase; that mechanical change has no screener
  value and would add migration/script risk. Product-facing names must still be corrected.

**Exit criteria**

- The application starts directly in the research/screener workspace and makes no strategy API
  request.
- `/api/v1/strategies` is no longer registered.
- Fresh and existing databases migrate to the new head; downgrade recreates the two empty legacy
  tables without inventing data.
- Backend tests, Ruff, strict mypy, frontend tests, and frontend build pass.
- No executable strategy-builder code remains.

## Phase 1 — screener ranking domain and API (initial release implemented)

**Deliverables**

- A typed, read-only screener service independent of HTTP and UI layers.
- A paginated endpoint such as `GET /api/v1/data/screener` supporting:
  - one, multiple, or all active fund houses;
  - one or more exact AMFI classifications;
  - Direct or Regular plan selection, defaulting to Direct;
  - Growth option selection for the first release;
  - horizons of 1 month, 3 months, 6 months, 1 year (default), 3 years, 5 years, and 10 years;
  - explicit `as_of`, endpoint tolerance, and minimum-history inputs;
  - scheme-name, AMFI-code, or ISIN search; and
  - deterministic sorting by performance, fund house, scheme name, or AMFI code.
- A response row containing AMFI code, scheme name, fund house, classification, plan/option,
  start/end NAV and dates, elapsed days, total return, CAGR where applicable, endpoint staleness,
  and current data-quality status.
- Response-level metadata containing the requested filters, actual dataset date, return basis,
  distribution treatment, day-count convention, endpoint rules, total eligible rows, and exclusion
  counts by reason.
- Set-based database access suitable for the full local universe. Do not load the complete NAV
  history separately for every option.

**Exit criteria**

- Hand-calculated fixtures independently verify absolute return, CAGR, anniversary selection,
  holiday tolerance, stale endpoints, insufficient history, and deterministic ties.
- Direct and Regular options never collapse into one row, and IDCW cannot enter the initial Growth
  ranking accidentally.
- Pagination and sorting are stable for equal returns.
- Query performance is measured against the local dataset before adding caches or summary tables.
- API integration tests cover valid filters, empty results, invalid dates/horizons, and stale or
  insufficient data.

## Phase 2 — customized screener interface (initial release implemented)

**Deliverables**

- Make the screener the application home view.
- Multi-select fund-house filters with “all fund houses” as the default.
- Exact classification, plan, horizon, as-of date, search, and minimum-history controls.
- A sortable, paginated result grid showing rank, fund, fund house, AMFI code, classification,
  actual period, return, CAGR/absolute-return label, latest NAV date, and quality/freshness status.
- Clear loading, empty, invalid-filter, partial-data, and API-error states.
- URL-backed filter state so a screen can be bookmarked or shared locally without a saved-strategy
  subsystem.
- Row drill-down that reuses the existing NAV performance, drawdown, IDCW evidence, and provenance
  views.
- Visible methodology and limitations beside the ranking rather than hidden in documentation.

**Exit criteria**

- A user can compare Direct Growth funds across all 57 current fund houses without selecting one
  fund house first.
- Every displayed return can be traced to its exact AMFI code and start/end NAV observations.
- Filter changes cannot show stale results from an earlier request.
- Frontend schema validation rejects malformed API responses.
- Component tests cover filters, sorting, pagination, exclusions, empty state, and drill-down.

The initial release implements the default landing screen, stable canonical classification IDs,
an audited source-alias reference and non-mutating similarity report, and a separate audited local
alias layer for concise screener labels and reviewed predecessor/successor grouping. The local
mapping is editable under the top-level **Aliases** navigation item without rewriting AMFI source
text. Every active canonical classification has an explicit alias; classifications outside a
reviewed group use audited singleton aliases. Audited deactivation releases all members for
explicit reassignment. The release also includes all/single fund-house filtering, Direct/Regular
separation, seven performance horizons, search, stable return ranking, exclusions, pagination, and
comparison of up to five funds. The existing data workspace is available under the top-level
**Data** navigation item. NAV comparison supports normalized-to-100 and raw-NAV views; canonical
IDCW cash events are annotated at record date without being added to NAV returns. The visible
methodology note includes the classification mapping version. URL-backed screener filters, sortable
columns, frontend interaction tests, and the environment-blocked data-API integration rerun remain
before this phase is considered fully closed.

The selector also exposes explicit mutual-fund, index-fund, and ETF product facets. Index funds and
ETFs remain AMFI option-level NAV rankings. Official Nifty price, GTR, and NTR series can be selected
as separately labelled standalone references with independently reported actual endpoints and
staleness; they do not alter rank or claim excess return across unmatched dates.

## Phase 3 — richer screening metrics

Add metrics only after the primary trailing-return ranking is correct and measured.

**Candidate deliverables**

- maximum drawdown over the selected period;
- annualized volatility with an explicit observation frequency and annualization convention;
- rolling-return consistency, including median, minimum, and positive-period percentage;
- return-versus-drawdown and return-versus-volatility sorting;
- multiple-horizon columns for side-by-side consistency checks; and
- benchmark-relative return only where a suitable, explicitly selected total-return index and an
  exact common-date alignment rule exist; standalone benchmark return is already available.

**Exit criteria**

- Every metric has an independently calculable fixture and a displayed definition.
- Metrics with invalid prerequisites are omitted or explicitly unavailable, never coerced to zero.
- Benchmark price, gross total-return, and net total-return series are never treated as
  interchangeable.

## Phase 4 — reproducibility and operational hardening

**Deliverables**

- Immutable dataset-snapshot manifest tying NAV, scheme metadata, lifecycle, parser, code, and
  quality versions to an exported screener result.
- CSV export containing visible columns, exact filter configuration, methodology metadata, and
  snapshot identifier.
- Query-plan and latency measurements for common full-universe filters.
- Index or precomputed-summary changes only when measurements justify them and invalidation rules
  are explicit.
- Database backup/restore and migration tests from both an empty database and the previous head.
- High-value browser tests for ranking, drill-down, bookmark restoration, error handling, and
  export.

**Exit criteria**

- The same filters and dataset snapshot reproduce the same ordered results.
- Exported rankings disclose actual NAV dates and all calculation conventions.
- No known high-severity correctness, provenance, security, or data-integrity defect remains.

## Main risks

1. **False comparability:** mixing IDCW with Growth, Direct with Regular, or unrelated categories
   can produce a numerically correct but economically misleading ranking.
2. **Endpoint bias:** stale or mismatched start/end NAV dates can change rank order. Actual dates,
   elapsed days, and exclusions must remain visible.
3. **Current-universe bias:** current metadata and catalogs do not establish a survivorship-free
   historical universe. The first screener is a current-universe research view.
4. **Category inconsistency:** source classifications and historical renames may not be comparable
   across time. Use exact retained classifications until an auditable normalization exists.
5. **Outliers and source revisions:** extreme returns may reflect genuine events, scheme changes,
   or source anomalies. Preserve drill-down and quality evidence instead of silently winsorizing.
6. **Scale:** ranking tens of thousands of options over tens of millions of NAV rows can encourage
   premature caching. Measure the set-based query first and define invalidation before caching.
