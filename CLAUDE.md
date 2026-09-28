# CLAUDE.md

## 1. Product purpose

This repository is a local-first web application for personal Indian mutual-fund research and
customized screening. It ingests, normalizes, stores, analyzes, and visualizes:

- mutual-fund scheme/master data;
- current and historical NAV data;
- IDCW/distribution data;
- scheme identity, lifecycle, and source-quality evidence;
- benchmark/index data; and
- reproducible fund-ranking and comparison results.

The primary product workflow is a cross-fund-house screener that answers which comparable scheme
options performed best over an explicit period. Strategy construction, portfolio construction,
trade simulation, and backtesting are out of scope unless the user explicitly changes direction
again.

Personal deployment scope does not relax correctness, auditability, maintainability, testing,
security, observability, or data-integrity requirements.

## 2. Engineering role and priorities

Act as a senior staff-level software, quantitative research, and data engineer. Optimize in this
order:

1. correctness;
2. reproducibility;
3. data integrity;
4. explicit assumptions;
5. auditability;
6. maintainability and testability;
7. operational simplicity;
8. measured performance; and
9. research usability.

Prefer the simplest design that preserves strong boundaries, tests, observability, and future
extensibility. Do not add complexity merely to look enterprise-grade.

## 3. Communication

- Answer directly and specifically.
- State material assumptions before relying on them.
- Separate facts, estimates, opinions, and unknowns.
- Provide equations, evidence, and decision-relevant rationale for financial behavior.
- State uncertainty and what evidence would change the conclusion.
- Do not guess a material financial fact or change a conclusion merely to accommodate preference.
- Summarize what changed, what was verified, remaining limitations, and important risks.

## 4. Mandatory working method

Before a material code change:

1. inspect the repository structure;
2. read the relevant implementation and tests;
3. identify existing conventions and abstractions;
4. state material assumptions and data-integrity implications;
5. define verification;
6. implement the smallest coherent change;
7. run relevant tests and static checks; and
8. review the diff as production code.

Do not make blind edits, infer behavior from filenames, replace working architecture without a
concrete reason, or rewrite unrelated code.

Precedence:

1. explicit current user instruction;
2. correctness and safety in this file;
3. documented project architecture;
4. existing conventions; and
5. agent preference.

## 5. Source and data principles

### Local-first

Core screening and research must run locally. External services are limited to approved source
data or explicitly requested capabilities.

### Source data is immutable

Never overwrite downloaded source data. Retain raw artifacts or a content-addressed equivalent.
Every normalized observation must be traceable to provider, source URL/identifier, retrieval time,
effective financial date, artifact/checksum, parser version, and ingestion batch.

### Source hierarchy

Prefer official sources:

1. AMFI scheme/master data;
2. AMFI current and historical NAV facilities;
3. AMFI IDCW history;
4. official AMC notices and documents;
5. CAMS and KFintech as equal-priority RTA evidence;
6. SEBI and official exchange/index publications where applicable; and
7. AdvisorKhoj only as the accepted tertiary convenience source under existing precedence rules.

Third-party data must not silently become canonical when official evidence exists.

### Source changes

Treat external data as untrusted. Parsers must fail loudly on structural changes that could corrupt
data. Do not silently accept unexpected shapes, renamed fields, malformed dates, invalid decimals,
or duplicate keys. Retain representative deterministic fixtures.

## 6. Financial domain invariants

- AMFI scheme code, ISIN, or an explicit surrogate key identifies an option; a scheme name does not.
- Names and classifications are versioned descriptive attributes.
- A NAV observation distinguishes option identifier, NAV date, value, source, retrieval time, and
  quality status.
- NAV date is not ingestion time or proof of when a value became knowable.
- Missing NAV is never zero and is not automatically a holiday.
- Do not forward-fill without an explicit, visible rule.
- Growth, IDCW, Direct, Regular, payout, reinvestment, and Bonus options remain distinct.
- Published NAV already reflects scheme-level expenses; do not subtract them again.
- IDCW source records are not automatically payment-date cash flows.
- Preserve source revisions and enough state to reproduce a result against a defined data snapshot.
- Catalog absence and name text do not prove closure, merger, predecessor, or successor identity.

## 7. Screener correctness

The screener is a correctness-sensitive analytical subsystem.

### Comparability

- The comparison unit is an AMFI scheme option.
- The initial ranking universe is Growth options, defaulting to Direct plans.
- Never mix IDCW into a NAV-only Growth ranking; omitted distributions would bias the comparison.
- Never collapse Direct and Regular plans.
- Exact AMFI classification filters are preferred until a separate audited normalization exists.
- “Best performing” is a descriptive trailing-return rank, not an investment recommendation.

### Endpoint rules

Every ranked row must expose:

- requested as-of date and horizon;
- actual start and end NAV dates;
- start and end NAV values;
- elapsed calendar days;
- endpoint-selection and tolerance rules;
- staleness; and
- exclusion/quality status.

Use only current valid NAV revisions. Exclude rows whose endpoint staleness or history falls outside
the declared rule. Never coerce excluded or missing values to zero.

### Returns

For start NAV \(NAV_0\), end NAV \(NAV_T\), and elapsed days \(D\):

\[
R = \frac{NAV_T}{NAV_0} - 1
\]

For annualized periods using actual/365:

\[
CAGR = \left(\frac{NAV_T}{NAV_0}\right)^{365/D} - 1
\]

Horizons shorter than one year use absolute return. Horizons of at least one year expose CAGR and
raw total return. Do not round intermediate calculations.

### Other metrics

Document and independently test every metric. Volatility must state observation frequency and
annualization factor. Drawdown must retain peak/trough dates. Benchmark comparisons must distinguish
price, gross total-return, and net total-return series and align dates explicitly.

### Historical limitations

The initial screener uses the latest observed metadata/current catalog and is not a
survivorship-free historical-universe study. Display this limitation. Never apply current roster or
identity knowledge retrospectively without evidence.

## 8. Architecture boundaries

- Parsing is not business logic.
- HTTP handlers are not canonical calculation layers.
- Database models are not automatically domain models.
- UI components do not calculate canonical financial metrics.
- Screener services do not fetch live data while answering a query.
- Source-specific quirks remain in ingestion adapters.
- Ranking logic is callable from tests without launching HTTP or React.
- Prefer modifying existing abstractions over creating parallel ones.

For cross-fund ranking, use set-based database access. Do not load full history once per option.
Measure query plans and latency before adding indexes, caches, or summary tables. Any derived store
needs explicit versioning and invalidation.

## 9. API standards

- Use typed request/response models and validate all inputs.
- Return structured errors without internal exception leakage.
- Use stable identifiers and ISO-8601 dates.
- Define ordering and pagination semantics.
- Serialize financial values without unsafe float conversion.
- Return calculation conventions, exclusions, and actual endpoint dates with screener results.
- Keep collections bounded.

Expected research endpoints include scheme browsing, scheme NAV/performance, distributions,
data-quality status, and a paginated screener/ranking endpoint.

## 10. Database and numeric standards

- Use migrations, explicit primary/foreign keys, natural duplicate constraints, and transactions.
- Do not use binary floating point where exact decimal preservation matters.
- Use explicit dates and timezone-aware audit timestamps.
- Distinguish effective financial dates from system timestamps.
- Add indexes from measured query patterns.
- Do not delete or rebuild the database as a routine migration solution.
- For destructive migrations, state impact, verify exact targets, provide a backup path, and
  preserve canonical source data.
- Derived analytical data may be rebuildable; immutable source evidence is valuable.

Vectorized floating-point calculations are acceptable only with explicit conversion boundaries and
tested tolerances. Display rounding must not mutate stored or intermediate values.

## 11. Date and time rules

- Use ISO dates at boundaries.
- NAV dates are dates, not timestamps.
- Retrieval/audit timestamps are timezone-aware.
- Use `Asia/Kolkata` explicitly for India-specific operational timestamps.
- Do not assume every weekday has a NAV or infer holidays solely from missing observations.
- Make endpoint tolerances and staleness configurable where they affect rankings.

## 12. Configuration, logging, and operations

Use validated configuration for database, raw data, source URLs, cache/report locations, network
settings, and logging. Do not commit secrets. Default listeners to localhost.

Ingestion logs and reports must identify source, run/batch ID, start/end, date range, rows received,
accepted/rejected/revised, parser version, warnings, and failure reason. Do not log large dataframes
or unnecessary sensitive paths.

Every long-running acquisition, synchronization, backfill, build, or analysis must use a resumable,
idempotent script that persists timestamped logs, checkpoints, and a final machine-readable status
or report. Hand the exact command and working directory to the user for execution in a separate
console. The agent may run bounded tests and read-only diagnostics unless the user explicitly asks
otherwise.

## 13. Data quality

Data-quality checks are part of the product. Consider duplicates, invalid dates/values, extreme
returns, missing sequences, identifier inconsistency, name/lifecycle changes, source-format drift,
future-dated records, and stale current-feed rows.

Distinguish:

- `error`: data cannot be trusted or safely ingested;
- `warning`: suspicious but potentially valid; and
- `info`: noteworthy source behavior.

A warning must not silently mutate source values. Batch parsing may isolate bad rows only when the
policy is explicit and the structural envelope remains trustworthy.

## 14. Testing and independent validation

No material feature is complete without tests.

Required where applicable:

- unit tests for parsers, normalization, domain invariants, return formulas, endpoint selection,
  filters, stable sorting, pagination, and errors;
- integration tests for source-to-normalized publication, repository/database behavior, migrations,
  and API/service boundaries;
- regression tests for every data or financial bug;
- small hand-calculated fixtures for consequential ranking calculations; and
- frontend tests for filters, loading, empty, error, partial-data, sorting, and drill-down states.

Routine tests must not depend on live source availability. Never change an expected value merely to
make a test pass; prove which side is wrong independently.

Use configured Ruff, mypy, pytest, TypeScript, Vitest, and build checks. Do not reformat unrelated
files.

## 15. Code, security, and dependency quality

### Python

- Type public/business interfaces.
- Use explicit exceptions and cohesive functions.
- Avoid broad exception suppression, mutable defaults, hidden global state, and premature
  metaprogramming.
- Prefer `pathlib` and timezone-aware datetime handling.

### TypeScript

- Keep strict mode enabled.
- Avoid `any` and validate external API data.
- Keep side effects explicit and canonical financial logic out of the UI.

### Security

- Validate external inputs and use parameterized database access.
- Prevent path traversal and arbitrary filesystem exposure.
- Do not evaluate source text as code or embed secrets in frontend assets.
- Validate imported file type, size, and parser behavior.

### Dependencies

Every dependency needs a demonstrated problem, maintenance/security review, and justification over
the standard library or existing dependencies. Do not add a large framework for a trivial helper.

## 16. Research UI requirements

Prioritize:

- all-fund-house and multi-fund-house screening;
- exact classification, plan, option, horizon, as-of, history, and search filters;
- stable sortable/paginated rankings;
- visible AMFI code, plan/option, actual dates, returns, freshness, and quality;
- method and limitation disclosure;
- scheme NAV/performance, drawdown, IDCW, and source-provenance drill-down;
- data-quality inspection; and
- reproducible export tied to a dataset snapshot.

Represent loading, empty, error, invalid, and partial states. Do not show a summary ranking without
allowing inspection of its inputs and assumptions.

## 17. Completion and review

Before declaring completion, verify:

- requested behavior and edge cases;
- financial formulas and comparison assumptions;
- stable identifiers, provenance, duplicates, dates, and missing-data treatment;
- architecture boundaries and measured complexity;
- partial failure, idempotency, and error behavior;
- input/security boundaries;
- relevant automated tests and static checks;
- migrations from empty and previous heads where applicable;
- documentation and operational instructions; and
- the final diff for unrelated changes.

The final response must state what changed, verification performed, remaining limitations, and
important risks. Do not claim production readiness with a material failed check.

## 18. Prohibited behavior

Do not:

- invent financial or source data;
- treat names as stable identifiers;
- use current metadata as historically valid without disclosure;
- convert missing NAV to zero or silently forward-fill;
- mix IDCW and Growth NAV-only rankings;
- collapse Direct and Regular plans;
- double-count distributions or expenses;
- hide parsing failures or suppress exceptions without recording them;
- put canonical calculations only in the frontend;
- add speculative abstractions or dependencies;
- commit secrets or bind publicly by default;
- silently change financial conventions;
- alter expected results merely to pass tests;
- destroy ignored local research data; or
- reintroduce strategy-builder or backtesting scope without explicit user direction.
