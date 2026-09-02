# AdvisorKhoj tertiary distribution acquisition

## Scope and trust boundary

AdvisorKhoj is a third-party convenience source used to close historical IDCW discovery gaps. It
is not AMFI, an AMC, or an RTA, and its scheme names are not durable identifiers. The acquisition
therefore retains the complete catalog, exact source responses, normalized observations, and
append-only AMFI mapping reviews. Strictly mapped, positive INR-per-unit rows may now publish only
as tertiary canonical fallback evidence when no conflicting AMFI, exact official-AMC notice, CAMS,
or KFintech value exists.

The source date is retained as its stated dividend record date. Announcement date, ex-date,
payment date, cash availability, tax treatment, and reinvestment-unit allocation are not inferred.

## Complete acquisition

Run the complete catalog-driven workflow:

```bash
./scripts/sync_advisorkhoj_distributions.sh
```

The wrapper performs these independently verifiable stages:

1. discovers the AMC/category/scheme catalog exposed by the public `amc-wise-dividends` workflow;
2. stores a timestamped catalog containing every exact response and SHA-256 checksum;
3. captures one distribution-detail response for every distinct catalog scheme into JSONL;
4. rejects an incomplete, duplicate, or catalog-mismatched capture before normalization;
5. stores catalog and capture files through the content-addressed raw artifact store;
6. persists immutable catalog, capture, observation, provenance, and mapping-review records;
7. publishes eligible tertiary fallback rows with one commit per AMFI option; and
8. writes a new option-level distribution coverage snapshot.

The collector uses bounded concurrency and never overwrites an earlier output:

```bash
MFST_ADVISORKHOJ_WORKERS=6 \
MFST_ADVISORKHOJ_DELAY_SECONDS=0.1 \
./scripts/sync_advisorkhoj_distributions.sh
```

Interrupted full captures can resume into a new immutable file:

```bash
backend/.venv/bin/python scripts/capture_advisorkhoj_idcw.py \
  --catalog-file /path/to/catalog.json \
  --resume-from /path/to/partial.jsonl \
  --output /path/to/completed.jsonl \
  --workers 6
```

The import and identity review can also be run separately:

```bash
backend/.venv/bin/mfst acquire-advisorkhoj-distributions \
  --catalog-file /path/to/catalog.json \
  --capture-file /path/to/completed.jsonl

backend/.venv/bin/mfst refresh-advisorkhoj-mappings \
  --capture-file /path/to/completed.jsonl

backend/.venv/bin/mfst publish-pending-advisorkhoj-distributions
```

The refresh command appends new mapping decisions only. It does not create another ingestion batch
or duplicate source observations. Publication is independently resumable: each AMFI option commits
atomically, and linked or explicitly blocked source rows are skipped on rerun. After the
CAMS/KFintech batch has completed and been verified, publish the already captured local tertiary
dataset with:

```bash
./scripts/publish_advisorkhoj_fallback.sh
```

Review unique latest identity failures by row impact without changing mappings:

```bash
backend/.venv/bin/mfst distribution-identity-backlog-report --limit 100
```

For AdvisorKhoj, the report distinguishes no metadata candidate, insufficient NAV evidence,
comparable-date NAV conflict, and multiple qualifying NAV candidates. It retains capture and
mapping-review IDs so any later manual conclusion can cite separate official identifier or
predecessor evidence.

Set `MFST_ADVISORKHOJ_OPTION_LIMIT` to bound one invocation by AMFI option count. Rerun the same
script to continue.

## Source precedence

Canonical conflict resolution is deterministic:

1. AMFI or an exact-code official AMC notice;
2. CAMS and KFintech as equal-priority official RTA peers; and
3. AdvisorKhoj as tertiary fallback.

Existing exact-code official notice evidence retains its precedence, but collecting broader AMC
documentary evidence is optional and does not gate IDCW acquisition completion for this local
research dataset.

A later higher-priority observation supersedes a lower-priority current revision while preserving
both immutable revisions and their provenance. A lower-priority disagreement is retained as a
blocked issue. Conflicting sources at the same tier leave no arbitrary current value.

## Parser and data-quality policy

The catalog parser validates exact provider/schema fields, response checksums, the complete
AMC/query hierarchy, and the denormalized scheme list. The detail parser requires the exact four
source fields, strict `DD-MM-YYYY` dates in descending order without duplicates, decimal amount/NAV
and yield values, and an amount/NAV/yield equation match when NAV is non-zero.

Observed source anomalies are retained rather than rewritten. Positive, zero, and negative amounts,
zero reference NAV, dates earlier than the first Indian mutual fund in 1964, and dates after capture
are counted separately. Only positive rows with `quality_status=valid` and record dates on or after
1964-01-01 are publication candidates. Literal pre-1964 dates remain immutable source rows and are
recorded as `implausible_record_date` publication issues; no replacement date is inferred.

## Identity policy

Names generate candidates; only AMFI scheme codes identify options. Automatic mapping requires:

1. compatible AMC, IDCW option, and explicit Direct/Regular plan;
2. compatible explicit payout/reinvestment and distribution-frequency qualifiers;
3. either three exact AMFI NAV matches spanning at least 30 days, or an exact normalized name plus
   at least one exact NAV match;
4. no comparable-date NAV conflict; and
5. exactly one qualifying AMFI scheme code.

Missing AMFI dates are not zero-filled or forward-filled. Payout and reinvestment options with the
same NAV remain ambiguous unless source qualifiers distinguish them. Every decision retains its
candidate codes, sampled dates, matches, conflicts, and evidence span as append-only JSON evidence.

## 20 August 2026 acquisition result

The validated catalog contained 68 AMCs, 1,280 AMC/category queries, and 4,442 distinct scheme
histories. All 4,442 responses were captured and imported, producing 1,066,137 immutable source
rows:

- 1,066,096 positive-amount observations;
- 40 zero-amount observations;
- 1 negative-amount observation;
- 1 zero-reference-NAV observation;
- 7 implausible pre-1964 dates; and
- 1 future-dated observation, which is also the zero-NAV row.

The mapping review classified 2,243 nonempty captures as mapped, 27 as ambiguous, and 396 as
unresolved. Another 1,776 catalog entries returned an empty history and remain explicitly
unresolved. Mapped captures represent 761,687 source rows and 2,243 distinct AMFI scheme codes.
Canonical events published at initial acquisition: zero. This is the dated pre-fallback baseline.
The operator publication batch subsequently ran and was accepted; its authoritative report is
`data/advisorkhoj-reports/publication-20260824T123229Z.json`, and current combined counts are in
`docs/CURRENT_STATUS.md`.

Raw artifact identifiers:

- catalog SHA-256: `8d599fe8aa4d70bee3c3bcdff0e7e8bcc0e74c3b664f2a622aaa03c7e310c6a3`;
- complete capture SHA-256: `ecfc35a8125c9c5bba22a52f694f7021e8e0824210548582a5b679d170ae397b`;
- catalog batch: `19302e56-abd1-425e-a03a-05b499844768`; and
- capture batch: `63ebd82c-32a1-46c3-a46d-4cb982274b75`.

At acquisition time, coverage run `edb5cd89-3115-4925-bab6-6c81e48ad0e3` assessed 16,236 current
IDCW options: 1,678 had canonical events, 1,914 had blocked source evidence, and 12,644 remained
unverified-empty. The later combined assessment is recorded in `docs/DISTRIBUTION_COVERAGE.md`.

## Acquisition exit and remaining limitations

The finite AdvisorKhoj catalog is fully captured and queryable, so this work is no longer a blocker
for core research/backtest development. The 423 nonempty ambiguous/unresolved captures are an
explicit identity-review backlog, not silently discarded data.

- The source catalog is a current public inventory, not a survivorship-free historical universe.
- Mapping uses current valid AMFI NAV revisions; immutable source evidence is retained, but a formal
  AMFI NAV snapshot identifier is still required before use in a reproducible backtest.
- Tertiary amounts may be used only through the audited fallback publication links after the
  AMFI/CAMS/KFintech precedence checks; raw AdvisorKhoj observations are not cash flows by
  themselves.
- Future refreshes must be immutable snapshots so upstream additions and corrections remain
  detectable.
- Backtests should normally use Growth NAV for total-return research. Modeling IDCW requires an
  explicit, trust-aware cash-flow policy that prevents distribution double counting.
