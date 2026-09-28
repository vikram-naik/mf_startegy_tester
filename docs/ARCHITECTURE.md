# Architecture decisions

## ADR-001: local SQLite with explicit migrations

**Status:** accepted.

SQLite minimizes local operations for a single-user application. Repository and service boundaries
own database access, and Alembic migrations evolve the schema. WAL and foreign-key enforcement are
enabled per connection. PostgreSQL remains deferred until measured concurrency, query, or
operational requirements justify it.

## ADR-002: immutable source artifacts and normalized provenance

**Status:** required.

Downloads are stored content-addressably and never overwritten. Every normalized observation is
traceable to its artifact, ingestion batch, provider, retrieval timestamp, effective financial
date, and parser version. A source correction creates another immutable revision rather than
rewriting prior evidence.

## ADR-003: screener calculations are typed backend services

**Status:** accepted for the screener product.

The UI submits validated ranking filters and displays typed results. It does not calculate
canonical returns or silently repair missing observations. A screener service owns endpoint
selection, staleness rules, return calculations, exclusions, stable sorting, and calculation
metadata independently of FastAPI and React.

The first ranking universe is Growth options, defaulting to Direct plans. IDCW options are excluded
because NAV-only returns omit distributions. Direct and Regular plans remain distinct AMFI scheme
options and are never collapsed into a single row.

Source classification text remains immutable. Canonical classifications and approved source
aliases are persisted separately with stable IDs, evidence, and a mapping version. NAV publication
registers new deterministic aliases; ranking and comparison expand a canonical ID back to every
approved raw label. Every active canonical classification has a local screener alias; reviewed
aliases may group several canonical classifications, while all other classifications use explicit
singleton aliases. This keeps the dropdown and alias-management vocabulary identical without
modifying either source layer. Alias membership, status, name, version, and change reason are
persisted in immutable revisions. Similarity produces review proposals only; it never mutates
either alias layer.

## ADR-004: set-based ranking before caching

**Status:** accepted.

Cross-fund-house ranking must not issue one full-history query per option. The initial
implementation will use set-based database access to resolve eligible options and their exact
start/end NAV observations. Query plans and local latency will be measured before adding indexes,
summary tables, or caches. Any derived store must have explicit versioning and invalidation tied to
NAV and metadata revisions.

## ADR-005: heatmaps aggregate explicit option and series returns

**Status:** accepted.

Fund heatmaps reuse the current screener alias vocabulary and calculate one return per eligible
Growth option before taking the classification median. Direct and Regular plans remain separate.
Trailing endpoint selection is set based. Rolling fund calculations stream each selected option's
valid current history in one ordered result and sample the last valid NAV in each calendar month;
they do not issue one full-history query per option. The category value is the median of per-option
rolling medians so scheme age does not implicitly determine category weight.

Official Nifty total-return series are the benchmark universe and official Nifty price series are
the index universe. Each remains an individual tile with its return basis exposed. The UI only
renders typed values and colors; canonical endpoint selection, return calculation, aggregation,
and exclusions stay in the backend service. See [the heatmap methodology](HEATMAPS.md).

## Key invariants

- AMFI scheme codes are stable option identifiers; scheme names are descriptive attributes.
- Missing NAV is distinct from zero, a valuation holiday, and an ingestion failure.
- Growth and IDCW histories are distinct options and cannot share a NAV-only performance ranking.
- Direct and Regular options remain distinct and must be labeled.
- Canonical source aliases and local screener aliases affect query resolution only; original AMFI
  metadata is retained.
- Return rows expose actual start/end NAV dates, elapsed days, and staleness.
- Rankings use only valid current NAV revisions; quarantined rows are excluded, never coerced.
- Current metadata does not prove a survivorship-free historical universe.
- Every displayed ranking states its horizon, return basis, distribution treatment, endpoint rule,
  and day-count convention.
