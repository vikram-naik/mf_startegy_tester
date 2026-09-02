# Distribution identifier review

**Review date:** 2026-08-17 (`Asia/Kolkata`)

**Historical checkpoint:** Counts below describe the 17 August source snapshot and initial
normalization. Later parser/source versions retain 208 source-row versions across the same 21
source-only identifiers. Use `docs/CURRENT_STATUS.md` and the latest
`distribution-identity-backlog-report` for current counts; the evidence rules and prohibition on
name-only mapping below remain current.

## Scope and conclusion

The complete AMFI distribution snapshot contains 103 rows across 21 `SD_ID` values that are not
present as exact `scheme_options.amfi_scheme_code` values in the completed NAV dataset. All 21 have
been recorded as `source_only` in the append-only `distribution_identifier_reviews` ledger.

`source_only` has a deliberately narrow meaning: the identifier occurs in an immutable official
AMFI scheme-dividend artifact, but there is no identical code in the ingested AMFI NAV option
universe. It does not assert that the identifier is economically equivalent to a similarly named
NAV option. No identifier has been marked `mapped` or `source_error`.

## Evidence and important exceptions

- The 21 values are exact AMFI `SD_ID` fields, not identifiers parsed from names.
- Several source families contain both matched and unmatched option IDs. Similar or nearly
  identical names cannot establish an alias. For example, Edelweiss option `118731` and matched
  option `118732` have overlapping distribution histories.
- The current [AMFI scheme summary for family 129](https://portal.amfiindia.com/spages/SSD_129.pdf)
  lists codes `120596`, `120597`, `100349`, and `100348`; it does not list historical distribution
  ID `120286`.
- The current [AMFI scheme summary for family 11874](https://portal.amfiindia.com/spages/SSD_11874.pdf)
  lists `146795`, `146797`, and `146800`; the distribution endpoint nevertheless contains one
  historical row for `146796`. This conflict must remain visible rather than being silently mapped
  to `146800`.

An identity can move from `source_only` to `mapped` only when another immutable official artifact
explicitly establishes continuity with an existing AMFI NAV scheme code. A scheme-dividend
artifact alone is rejected as mapping or source-error evidence because it proves occurrence, not
cross-identifier equivalence.

## Distribution-value gate

The source dataset contains 225 rows across 114 option identifiers whose scalar distribution value
is zero. These rows remain immutable source observations. They must not become canonical cash-flow
events unless an official source establishes a non-zero payout or documented non-cash event
semantics. The issue is broader than the 21 source-only identifiers and includes some records whose
names indicate Growth or Bonus options.

## Normalization-candidate gate

`mfst data-quality-report` now partitions every accepted AMFI distribution source row into one
normalization-gate category. This is a read-only assessment; it neither changes the immutable
source row nor publishes a canonical `DistributionEvent`.

A row is a `cash_amount_candidate` only when all of the following are true:

1. `source_option_id` exactly matches an ingested AMFI NAV scheme code;
2. the source unit is `amount`;
3. the amount is positive; and
4. the source scheme/NAV labels contain explicit dividend/IDCW option terminology without Bonus,
   Growth, Cumulative, or conflicting markers.

The label classifier removes the source `scheme_name` only when it is an exact normalized prefix
of `nav_name`. This prevents a proper fund name such as `... Growth Fund ...` or `Dividend Yield
Fund` from being treated as option semantics. When names do not share that boundary, only explicit
marker differences and narrow option phrases are accepted. Frequency-only historical labels such
as `Daily`, `Weekly`, `Fortnightly`, and `Monthly` remain `unknown_option_label`; frequency is not
proof of a cash distribution.

The 2026-08-17 snapshot partitions all 36,965 accepted rows as follows:

| Gate category | Rows | Distinct source option IDs |
| --- | ---: | ---: |
| `cash_amount_candidate` | 30,939 | 1,667 |
| `unmatched_option_identifier` | 103 | 21 |
| `non_positive_scalar` | 186 | 109 |
| `percentage_source_value` | 1,747 | 237 |
| `ratio_source_value` | 1 | 1 |
| `explicit_bonus_option` | 283 | 7 |
| `explicit_growth_or_cumulative_option` | 25 | 22 |
| `ambiguous_bonus_or_distribution_option` | 264 | 2 |
| `conflicting_option_labels` | 0 | 0 |
| `unknown_option_label` | 3,417 | 6 |

Rows are disjoint and sum to 36,965. Identifier counts are not additive because one identifier may
have rows in multiple value/unit categories. The report includes option-level source names for
every label-based blocker so the small review set can be audited without weakening classification
rules.

## Canonical event publication

Publish the gated candidates with:

```bash
uv run --project backend mfst normalize-distributions
```

Normalization version `amfi-distribution-2026.08.1` publishes a stable `distribution_events`
identity keyed by exact AMFI scheme code, AMFI `record_date`, and event type `idcw_cash`.
`distribution_event_revisions` stores the positive INR amount per unit as lossless decimal text;
one partial unique index permits only one current revision per event. Each revision is connected to
the exact immutable `amfi_distribution_records` row through
`distribution_event_revision_sources`. A later AMFI amount correction creates another immutable
revision instead of overwriting the previous value.

The operation is atomic, audited in `distribution_normalization_runs`, and idempotent. It refuses
to publish when candidate and blocked source rows share one event identity. The first live run
created 30,939 events, revisions, and source links; an immediate second run inserted zero rows and
reported all 30,939 candidates unchanged.

The current snapshot has no duplicate `(source_option_id, record_date)` groups across any source
unit or label category. If a future source refresh introduces candidate and blocked rows under the
same natural identity, normalization fails for explicit review; it does not guess whether the rows
are corrections or separate economic events.

The source field is preserved strictly as `record_date`. No ex-date, announcement timestamp,
payment date, reinvestment date, information-availability time, or investor cash flow is inferred.
A canonical event is therefore suitable for source-data inspection, but it must not yet be posted
to portfolio cash accounting.

## Commands

Record idempotent `source_only` reviews for every currently unmatched identifier:

```bash
uv run --project backend mfst survey-distribution-identifiers
```

Append a later evidence-backed review:

```bash
uv run --project backend mfst review-distribution-identifier \
  --source-option-id 120286 \
  --status mapped \
  --matched-amfi-scheme-code 120597 \
  --evidence-batch-id 00000000-0000-0000-0000-000000000000 \
  --evidence-details "Official artifact explicitly establishes identifier continuity"
```

The example demonstrates command shape only; it is not evidence that `120286` maps to `120597`.
Do not execute it without the required official artifact.
