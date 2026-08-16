# Architecture decisions

## ADR-001: local SQLite with explicit migrations

**Status:** accepted for the bootstrap.

SQLite minimizes local operations for a single-user application. It is accessed only through repository boundaries and evolved only through Alembic migrations. WAL and foreign-key enforcement are enabled per connection. PostgreSQL is deferred until concurrent writers, dataset size, or analytical query evidence warrants it.

## ADR-002: immutable source artifacts and normalized provenance

**Status:** required for the ingestion phase.

Downloads will be stored content-addressably and never overwritten. Every normalized observation will reference its artifact, ingestion batch, provider, retrieval timestamp, effective financial date, and parser version. A source revision creates another source version; it does not rewrite the evidence used by an earlier backtest.

## ADR-003: declarative, versioned strategy documents

**Status:** implemented in the bootstrap.

The UI saves validated strategy documents rather than executable code. Documents separately describe universe selection, signals, selection, allocation, schedule, execution timing, cash flows, costs, and benchmark. Each edit creates an immutable revision. Backtest runs will reference the exact revision.

Supported building blocks can be combined without a deployment. A new primitive, such as a new metric or optimizer, still requires a reviewed engine addition. A later expression/graph capability can expand composability without allowing arbitrary code execution.

## ADR-004: point-in-time engine isolated from HTTP and storage

**Status:** planned.

The engine will receive an immutable strategy definition, a versioned data snapshot, and a valuation-calendar interface. It will never fetch live data during a run. Portfolio accounting and metrics will be callable directly from tests and CLI code.

## Key invariants

- Scheme names are descriptive, never durable identity keys.
- Missing NAV is distinct from zero and from a valuation holiday.
- Growth and IDCW histories are distinct instruments/options.
- Published mutual-fund NAV already reflects scheme expenses; those expenses are not deducted again.
- Strategy decisions use only information available at their simulated timestamps.
- Backtest results are invalid without strategy version, dataset snapshot, timing convention, and assumptions.
