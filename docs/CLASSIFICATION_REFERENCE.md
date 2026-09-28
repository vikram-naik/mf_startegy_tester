# Scheme classification reference

## Purpose

AMFI classification text is retained exactly in `scheme_metadata_versions`. The screener uses a
separate, audited reference so presentation drift does not split comparable options while source
evidence remains immutable.

Migration `20260902_0030` adds the immutable-source normalization layer:

- `scheme_classifications`: stable canonical IDs, display names, normalized keys, status, and
  mapping version; and
- `scheme_classification_aliases`: the exact AMFI source label, canonical ID, approved match type,
  evidence note, mapping version, and approval timestamp.

The mapping version is `amfi-classification-reference-2026.09.1`.

Migration `20260903_0031` adds a separate local presentation and grouping layer:

- `screener_classification_aliases`: concise names scoped by open-ended, close-ended, interval,
  or other structure;
- `screener_classification_alias_members`: a one-to-many mapping from one screener alias to
  canonical AMFI classifications; and
- `screener_classification_alias_revisions`: immutable snapshots of every alias name, status, and
  member change with its reason.

This separation is intentional. AMFI text and its source mapping remain unchanged when a local
screener alias is edited.

Migration `20260904_0032` gives every active canonical classification an explicit local alias.
Classifications outside a reviewed group receive a deterministic singleton alias and immutable
initial revision. Normal NAV publication applies the same rule to newly discovered canonical
classifications. Short labels are retained where unique; collisions use canonical family context
so distinct classifications never share an ambiguous alias name.

## Approval policy

Normal NAV publication registers previously unseen AMFI labels. Only deterministic equivalences
are approved automatically:

- whitespace, capitalization, and parenthesis presentation;
- exact `Equity Scheme`/`Equity Schemes` and `Hybrid Scheme`/`Hybrid Schemes` variants;
- `Midcap`/`Mid Cap`; and
- `Large and Mid Cap`/`Large & Mid Cap`.

The source label is never rewritten. Categories that remain semantically different receive
different canonical IDs. Examples that must remain separate include Large Cap versus Large & Mid
Cap, Long Duration versus Low Duration, and Gold ETF versus Debt ETF.

## Similarity review

Generate a bounded, read-only report with:

```bash
backend/.venv/bin/mfst classification-reference-report
```

Change the candidate threshold only for review:

```bash
backend/.venv/bin/mfst classification-reference-report --proposal-threshold 0.90
```

The report uses normalized lexical and token similarity after rejecting pairs with conflicting
open/close/interval structure or broad asset class. A proposal never changes an alias. Embeddings
are deliberately not used: the current label set is small, and semantically different financial
categories often have extremely similar language.

An additional equivalence must be reviewed and delivered through a new mapping version and tested
migration. Direct database edits are not an accepted approval workflow.

## Screener aliases

The seeded aliases consolidate reviewed predecessor/successor labels introduced by the February
2026 SEBI classification transition. They cover the debt-category name changes, ELSS - Tax Saver,
three renamed hybrid categories, the legacy/current Gold ETF labels, solution-oriented label
variants, and the coarse domestic/overseas FoF labels.

The reviewed `Sectoral / Thematic Equity` alias combines the broad Sectoral/Thematic equity label
with the more specific Sectoral Fund and Thematic Fund labels. This is a presentation and query
group only: the three canonical AMFI classifications and every scheme option remain distinct. The
similarly named Income/Debt Oriented Sectoral Fund classification is deliberately excluded.

The seed otherwise does not combine categories where an AMFI label is broader than its possible
successors. In particular, generic Index Funds remains separate from Equity/Debt/Hybrid Index
Funds, and generic Other ETFs remains separate from asset-specific ETF categories.

Use the **Aliases** screen to create or edit a mapping. A save requires a reason, creates a full
immutable revision, and uses an expected version to reject concurrent stale edits. One canonical
AMFI classification can belong to at most one screener alias, and mappings cannot cross scheme
structures. An active alias must retain at least one member. Setting an alias inactive atomically
releases every member for reassignment and records an empty-member immutable revision; the
screener dropdown omits those temporarily unassigned classifications until they receive another
active alias. Reactivation requires an explicit selection of currently unassigned classifications.
Deactivation never deletes revision history or source data.

## Query behavior

The screener classification API returns only stable active screener-alias IDs. Display text is the
configured alias name, including for aliases with exactly one canonical member. Screener and
scheme-browser filters expand the selected ID through both mapping layers to all approved exact
AMFI labels. Comparison validation uses the same resolved group.

Screener responses include `classification_mapping_version`. An alias version changes whenever its
name, status, or members change, so an exported result can identify the grouping convention used.
The visible screener methodology note renders this version for the current result.

Each classification response also exposes a backend-derived `structure_type`: `open_ended`,
`close_ended`, `interval`, or `other`. The first three follow the explicit AMFI top-level label;
an unrecognized family remains `other` for visible review and is never guessed into a known type.
The screener uses this value to filter its classification selector.

The screener classification endpoint applies the same plan, horizon, actual-NAV endpoint, and
seven-calendar-day tolerance rules as the ranking endpoint. A classification with zero eligible
options is omitted. Each remaining classification reports candidate, eligible, and excluded option
counts; an optional fund-house filter scopes those counts to the selected AMC.

The ranking response retains aggregate exclusion counts and returns a separately paginated
`excluded_items` collection. Every excluded option identifies its AMFI code, name, fund house,
reason code, first/latest stored NAV dates, latest NAV value, staleness, and a human-readable
endpoint explanation. Missing or excluded values are never converted to zero.

The candidate universe and descriptive attributes come from the latest observed scheme metadata,
including for a historical `as_of` date. NAV endpoints still obey the requested historical date.
An option with no valid NAV on or before that date remains a visible stale exclusion rather than
disappearing from the current-universe candidate count. This is intentionally not a survivorship-
free historical-universe reconstruction.

An unknown source label is never guessed into an existing canonical classification; the reference
report lists it under `unmapped_source_labels`. Once registered as a new canonical classification,
it receives a singleton local alias rather than being merged with another category.

## Current local checkpoint

The source-normalization backfill retains 222 distinct historical AMFI labels as 222 approved
source aliases pointing to 103 canonical classifications. The local database has 71 screener
aliases and 71 immutable alias revisions; all 103 active canonical classifications have exactly one
alias membership. Twenty-five reviewed aliases retain their grouped mappings, one prior local
Silver ETF alias is preserved, and 45 previously unmapped classifications now have singleton
aliases. No canonical classification or raw AMFI alias was deleted or rewritten.

For Direct Growth over one year as of September 2, 2026, the screener now returns 54 concise
classification choices instead of 77 semantically split choices. For example, Corporate Bond has
26 candidates, 21 eligible options, and five explicit exclusions across its two retained AMFI
classification families.
