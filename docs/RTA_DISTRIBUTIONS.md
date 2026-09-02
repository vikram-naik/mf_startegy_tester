# CAMS and KFintech IDCW capture

## Scope and correctness boundary

AMFI's current distribution endpoint is not complete for many IDCW options. The RTA workflow adds
official CAMS and KFintech website observations without treating RTA names or proprietary codes as
AMFI identifiers.

The workflow first captures the official website response into resumable, source-containing JSONL.
It then imports the JSONL, maps the RTA option to an AMFI scheme code using exact evidence, and
publishes only safe INR-per-unit rows.

Source precedence is AMFI (plus exact-code official AMC notices), then CAMS/KFintech as
equal-priority peers, then AdvisorKhoj. RTA evidence supersedes a conflicting tertiary revision.
An AMFI/official conflict blocks the RTA row. A CAMS/KFintech disagreement retires the prior peer
value and leaves the event without an arbitrary current amount until higher-quality evidence
resolves it.

Broad official-AMC notice acquisition is optional. Completion for this local research dataset is
measured by exhausting AMFI, CAMS/KFintech, and AdvisorKhoj acquisition while retaining source
failures and unresolved identities explicitly.

## Operational completion checkpoint — 1 September 2026

The accepted-source RTA breadth phase is complete with explicit source limitations. The latest
database contains 1,658 CAMS captures / 40,849 rows and 4,107 KFintech captures / 643,469 rows.
Using only the latest review per capture, CAMS has 1,278 mapped, three ambiguous, and 377 unresolved
captures; KFintech has 748 mapped, 108 ambiguous, and 3,251 unresolved captures.

The final KFintech full traversal retained 226 structural scheme responses that contained neither
a recognized result nor an explicit empty marker, plus seven fund pages with no scheme roster. Its
only transport timeout was isolated and successfully captured with:

```bash
scripts/retry_kfintech_timeout.sh
```

That target returned two source rows but remains unresolved because no AMFI identity candidate
exists locally. The targeted status is
`data/rta-reports/kfintech-targeted-retry-20260901T001442Z.status` with `exit_status=0`.

Do not repeat the current multi-hour full retry merely to receive the same retained blank pages.
Retry a structural failure only after evidence of a source recovery or a parser change. Identity
resolution and blocked-row review are now normalization work, not acquisition work. See
`docs/CURRENT_STATUS.md` for the complete checkpoint.

Inspect the unique latest unresolved/ambiguous identity backlog without refreshing mappings or
publishing rows:

```bash
backend/.venv/bin/mfst distribution-identity-backlog-report --limit 100
```

The report distinguishes empty captures from nonempty source-row impact, groups the latest review
per capture by its evidence-backed rejection reason, and ranks capture/review IDs for later manual
official-evidence work. Source-row totals can overlap economically across providers or aliases and
must not be interpreted as unique payout events.

An RTA row is published only when the normalized scheme core, direct/regular plan, and an exact
current AMFI NAV observation agree. RTA codes remain separately queryable. Ambiguous or unresolved
options and amount conflicts are retained but blocked. An operator can append a manual mapping only
with documented evidence; mappings are never overwritten.

Scheme-core normalization removes only source presentation decorations that do not identify an
economic option, including CAMS reinvestment/exchange suffixes and obsolete-name clauses beginning
with `Formerly` or `erstwhile`. Distribution-frequency terms remain part of the identity because
Monthly, Quarterly, and similar options can have different AMFI codes. Exact dated NAV and plan
evidence remain mandatory; multiple matching AMFI codes produce a blocked ambiguous review.

The canonical amount is the CAMS `Retail` or KFintech `Individual` INR-per-unit value. CAMS
`Corporate` and KFintech `Non-Individual` values are retained separately and are not silently
substituted. The RTA date is stored as `record_date`; payment date and cash availability are not
inferred.

CAMS can render decimal values with binary-float tails. The parser retains that exact raw string and
normalizes a value to ten decimal places only when it has excess precision and differs from the
rounded value by at most `1e-12`. This bound is backed by the acquired corpus; any other value with
more than ten decimal places fails parsing. A parser correction creates a new normalized
source-row version instead of overwriting the prior record.
When the prior canonical RTA revision is linked to the same capture, date, raw amounts, and NAV
evidence, the corrected parser version explicitly supersedes that revision. It cannot use this path
to resolve a different RTA provider's value or a genuine raw-source disagreement.

## Run

Resume the initial capture and import for both RTAs:

```bash
./scripts/sync_rta_distributions.sh
```

The stable `*-full.jsonl` files make the initial run resumable. A new immutable observation cycle
uses new files:

```bash
MFST_RTA_CAPTURE_MODE=refresh ./scripts/sync_rta_distributions.sh
```

Provider selection and KFintech's historical boundary are configurable:

```bash
MFST_RTA_PROVIDERS=kfintech \
MFST_RTA_FROM_DATE=2000-01-01 \
./scripts/sync_rta_distributions.sh
```

The wrapper holds the same ingestion lock as the AMFI jobs, runs migrations, imports every valid
partial capture even if another scheme fails, then refreshes distribution coverage and the
data-quality report. Per-scheme extractor failures are appended to
`<capture-file>.errors.jsonl`; they do not cause later schemes to be skipped. These sidecars are
operator evidence and are not canonical distribution rows.

After five consecutive per-scheme failures, the CAMS collector abandons the remaining schemes for
that fund and continues with the next fund house. Only five consecutive fund-selector failures stop
the complete collector. These circuit breakers prevent a CAMS outage from consuming unbounded time
while preserving every already flushed JSONL capture. Override the shared bound only for diagnosis,
for example `MFST_CAMS_MAX_CONSECUTIVE_FAILURES=10`. Rerunning the same `full` command skips
successful scheme keys and retries the remaining gaps.

Previously retained CAMS timeout keys are deferred by default so breadth acquisition can continue
to later fund houses; they remain failed attempts, never empty observations. After completing the
remaining breadth pass, retry those keys explicitly with
`MFST_CAMS_RETRY_RETAINED_FAILURES=1`. New failures are never auto-deferred during the same run and
still open the five-consecutive-failure circuit.

CAMS fund-selector failures are retained separately in `<capture-file>.fund-errors.jsonl`; they no
longer terminate the complete collector at the first fund boundary. Structured capture progress is
also retained under `data/rta-reports/cams-capture-*.jsonl`, including discovered/loaded/failed fund
counts and the number of eligible IDCW schemes reached.

KFintech uses the same five-consecutive-failure default through
`MFST_KFINTECH_MAX_CONSECUTIVE_FAILURES`. Before making network requests, its collector verifies and
replays the latest source HTML retained for every failed scheme. A response that becomes valid
under a corrected strict parser is appended to the main capture with its original timestamp and
checksum; the error sidecar remains immutable audit evidence. Exact duplicate source rows are
collapsed, but distinct rows sharing a record date are retained independently because KFintech can
publish the same cash amount with different ex/cum NAV evidence.

A KFintech fund page whose scheme inventory is missing or malformed is retained verbatim in
`<capture-file>.fund-errors.jsonl`. That fund remains an explicit acquisition failure, while the
collector continues to later fund houses and returns a non-zero status after completing the breadth
pass. It is never interpreted as an empty IDCW roster.

After replay, a scheme whose latest nonempty, checksum-verified response still fails strict parsing
is deferred by default so a deterministic postback defect cannot repeatedly block later fund
houses. Deferred failures remain explicit in the completion summary and error sidecar; they are not
treated as empty captures. Force fresh network attempts only when investigating a source recovery:

```bash
MFST_KFINTECH_RETRY_RETAINED_FAILURES=1 \
MFST_RTA_PROVIDERS=kfintech \
./scripts/sync_rta_distributions.sh
```

If `Ctrl+C` interrupts mapping/publication after the importer has committed every capture and source
row, resume without another RTA request or duplicate source-row ingestion:

```bash
./scripts/resume_rta_distribution_import.sh data/rta-captures/kfintech-full.jsonl
```

The resume command verifies that every checksum-derived capture and row signature already exists
before it records mappings or publishes events. Mapping first restricts NAV history to exact AMFI
IDCW name/plan candidates; it never scans every IDCW option for each source evidence date. Progress
events are emitted during mapping and each 10,000-row publication interval.

If capture succeeded but a corrected parser rejected the import before creating an ingestion batch,
reparse the committed file offline. This appends corrected normalized source-row versions while
preserving the prior records and raw strings:

```bash
MFST_RTA_IMPORT_MODE=reparse \
./scripts/resume_rta_distribution_import.sh data/rta-captures/cams-full.jsonl
```

Individual stages are also available:

```bash
node scripts/capture_cams_idcw.mjs --output data/rta-captures/cams-full.jsonl
backend/.venv/bin/python scripts/capture_kfintech_idcw.py \
  --output data/rta-captures/kfintech-full.jsonl \
  --from-date 2000-01-01
backend/.venv/bin/mfst import-rta-distributions \
  --capture-file data/rta-captures/kfintech-full.jsonl
```

After an evidence-backed manual review:

```bash
backend/.venv/bin/mfst review-rta-scheme-mapping \
  --scheme-capture-id '<capture UUID>' \
  --amfi-scheme-code '<AMFI code>' \
  --evidence-details '<official identifier evidence and artifact reference>'
backend/.venv/bin/mfst publish-pending-rta-distributions
```

## Audit queries

Latest mapping and row coverage by RTA fund house:

```sql
WITH latest_mapping AS (
  SELECT *, ROW_NUMBER() OVER (
    PARTITION BY scheme_capture_id ORDER BY reviewed_at DESC, id DESC
  ) AS rank
  FROM rta_scheme_mapping_reviews
)
SELECT
  capture.provider,
  capture.rta_fund_code,
  capture.rta_fund_name,
  COUNT(*) AS scheme_captures,
  SUM(CASE WHEN mapping.status = 'mapped' THEN 1 ELSE 0 END) AS mapped_captures,
  SUM(CASE WHEN mapping.status = 'unresolved' THEN 1 ELSE 0 END) AS unresolved_captures,
  SUM(CASE WHEN mapping.status = 'ambiguous' THEN 1 ELSE 0 END) AS ambiguous_captures,
  SUM(capture.source_row_count) AS source_rows
FROM rta_scheme_captures AS capture
LEFT JOIN latest_mapping AS mapping
  ON mapping.scheme_capture_id = capture.id AND mapping.rank = 1
GROUP BY capture.provider, capture.rta_fund_code, capture.rta_fund_name
ORDER BY capture.provider, capture.rta_fund_name;
```

Unresolved options, including options whose official page returned no rows:

```sql
WITH latest_mapping AS (
  SELECT *, ROW_NUMBER() OVER (
    PARTITION BY scheme_capture_id ORDER BY reviewed_at DESC, id DESC
  ) AS rank
  FROM rta_scheme_mapping_reviews
)
SELECT
  capture.id,
  capture.provider,
  capture.rta_fund_name,
  capture.rta_scheme_code,
  capture.source_scheme_name,
  capture.plan_type,
  capture.source_row_count,
  mapping.status,
  mapping.evidence_details
FROM rta_scheme_captures AS capture
JOIN latest_mapping AS mapping
  ON mapping.scheme_capture_id = capture.id AND mapping.rank = 1
WHERE mapping.status <> 'mapped'
ORDER BY capture.provider, capture.rta_fund_name, capture.source_scheme_name;
```

Publication conflicts and blocked rows:

```sql
SELECT issue_code, COUNT(*) AS issue_count
FROM rta_distribution_issues
GROUP BY issue_code
ORDER BY issue_code;
```

## Source limitations

- CAMS renders data after decrypting an internal API response in its Angular application. The
  collector stores the rendered DOM and extracted tables. The public table currently exposes at
  most 50 IDCW rows per option; a successful CAMS capture is not proof of complete earlier history.
- KFintech exposes a historical date-range form. Its live table uses `Recorded Date`,
  `Dividend Rate - Individual`, and `Dividend Rate - Non Individual`; dates can use abbreviated
  month names, and zero is a legitimate retained Non-Individual value. Its proprietary fund and
  scheme codes are not AMFI codes. KFintech can list several spelling or payout/sweep labels for
  one proprietary request code. Those labels are retained as aliases and the code is queried once;
  conflicting Direct/Regular labels still fail loudly. A postback that does not retain the selected
  scheme is a retryable failure, not an empty result.
- An empty RTA table is captured, but it is not evidence that the option never paid IDCW outside the
  RTA page's available period.
- Literal record dates before 1964 are retained as source rows but quarantined from canonical
  events as `implausible_record_date`; the system never guesses a replacement date.
- Neither observed distribution UI supplies a reliable AMFI code or ISIN. Automatic publication
  therefore requires the exact name/plan/NAV evidence gate; unresolved options require official
  manual evidence.
- This workflow records payout declarations. It does not establish payment dates, announcement
  availability timestamps, tax treatment, or reinvestment unit allocations.

For a future genuinely partial breadth run, retry missing KFintech histories with:

```bash
scripts/retry_kfintech_idcw_failures.sh
```

The wrapper reuses the resumable full capture, retries retained structural responses, imports all
new rows, and writes a timestamped combined log plus a status file under `data/rta-reports/`. Its
retry-only circuit permits up to 100 consecutive failures so a small cluster of known blank
postbacks cannot prevent later fund rosters from being visited; the normal synchronization circuit
remains at five unless explicitly overridden. This command is not recommended for the current
completed checkpoint without new source/parser evidence.
