# Official source registry

Verified against the official AMFI website on 16 August 2026. Source formats and routes are treated
as versioned external contracts and are validated before any future normalized publication.

| Source | Official endpoint | Captured format | Parser policy |
| --- | --- | --- | --- |
| Complete latest NAV | `https://portal.amfiindia.com/spages/NAVAll.txt` | Semicolon-delimited text | Exact header/field count; non-negative decimal source value; explicit NAV date; duplicate scheme/date rejection |
| Historical NAV | `https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx` | Semicolon-delimited text | Maximum 90-day request; exact eight-field format; same value/date/duplicate checks |
| Scheme list by mutual fund | `https://www.amfiindia.com/api/populate-scheme` | JSON | Required exact identifier/name shape; numeric identifiers; duplicate rejection |
| Scheme details | `https://www.amfiindia.com/api/scheme-details` | JSON | Exact current field set; exact requested family identity; timezone-aware launch date; immutable family detail and launch-event provenance |
| Scheme distributions | `https://www.amfiindia.com/api/scheme-dividend` | JSON | Exact identity, option, date, year, source text, scalar decimal or structured ratio fields; immutable queryable snapshots, without cash-flow conversion |
| HDFC IDCW declaration notice | `https://files.hdfcfund.com/` | PDF | Official issuer marker, explicit record date, strict table shape, plan rows, one unambiguous INR-per-unit amount; structural ambiguity fails publication |
| HDFC scheme summary | `https://files.hdfcfund.com/` | PDF | Explicit AMFI-code, plan, and option mapping required as separate identity evidence for every notice row |
| CAMS NAV & IDCW history | `https://www.camsonline.com/InvestorServices/COL_ISNAV.aspx` | Browser-rendered HTML captured in JSONL | Proprietary scheme code retained; Retail amount is canonical candidate; Corporate amount retained separately; exact name/plan/NAV evidence required before AMFI mapping |
| KFintech dividend history | `https://mfs.kfintech.com/mfs/InvestorServices/NAVDividend/NAV_Dividend.aspx` | ASP.NET result HTML captured in JSONL | Proprietary fund/scheme codes retained; Individual amount is canonical candidate; Non-Individual and ex/cum NAV retained; exact name/plan/NAV evidence required before AMFI mapping |
| Nifty index mapping | `https://liveindexsa.niftyindices.com/assets/json/IndexMapping.json` | JSON | Exact mapping fields and unique trading names |
| Nifty price history | `https://www.niftyindices.com/BackPage/getHistoricaldatatabletoString` | JSON | Exact nested POST contract; positive OHLC; maximum 365 inclusive calendar days; returned index identity must equal the request |
| Nifty gross/net total-return history | `https://www.niftyindices.com/BackPage/getTotalReturnIndexString` | JSON | Gross TRI and optional NTR remain distinct series; exact date/index identity and positive values |
| NSE ETF master and daily report | `https://nsearchives.nseindia.com/` | CSV / ZIP | Browser-like NSE headers; strict current master and date-specific ETF-member schemas; current and legacy dated filenames |
| NSE/BSE cash-market bhavcopy | Official NSE archive and BSE Equity BhavCopy paths | CSV / ZIP | Exact UDiFF or legacy schema selected at 08-Jul-2024 boundary; positive OHLC, non-negative activity, exact exchange/date identity |
| BSE ETF market watch | `https://api.bseindia.com/BseIndiaAPI/api/ETFMarketwatchdatabeta/w` | JSON | Two observed exact schemas; broad roster accepted, categorized subset accepted only with explicit partial-coverage warning |

## Secondary convenience sources

| Source | Endpoint | Captured format | Publication policy |
| --- | --- | --- | --- |
| AdvisorKhoj historical dividends | `https://www.advisorkhoj.com/mutual-funds-research/amc-wise-dividends` | Checksummed catalog JSON plus one exact detail-API response per scheme in immutable JSONL | Strict shape/arithmetic validation, explicit option qualifiers, and multi-date AMFI NAV fingerprint; tertiary canonical fallback is allowed only after AMFI and CAMS/KFintech evidence under the audited precedence publisher |

AdvisorKhoj is not an official source and does not expose an AMFI code or ISIN in the captured
catalog/detail responses. Its short names are never durable identities. The acquisition and its limitations are documented in
`docs/ADVISORKHOJ_DISTRIBUTIONS.md`.

The AMFI distribution page states that records through 06 April 2009 are percentages and later
records are amounts. Some historical API values also carry an explicit trailing `%`. The source
record preserves that exact text separately from its decimal magnitude; an explicit marker takes
precedence, otherwise the published date convention determines `percentage` or `amount`. Neither
form is converted into a canonical cash flow.

One retained source row uses `1:3` rather than a scalar. The source representation labels it
`ratio` and stores integer numerator `1` and denominator `3`; scalar value is null. Taurus AMC's
[2008-09 annual report](https://www.taurusmutualfund.com/sites/default/files/downloads/Annual%20Report%202008-09.pdf)
independently identifies this October 2003 event as a bonus ratio. Canonical event classification
remains a separate normalization decision.

One UTI source row combines a percentage and an amount annotation as
`20% (Rs 2/- Per Unit`. The parser accepts only that narrowly validated grammar, storing percentage
`20` and annotated INR-per-unit amount `2` separately while retaining the complete original text.
The annotation is not treated as a canonical cash distribution. The observed `Per`/`per` casing
variation is accepted without relaxing the numeric or currency syntax.

One additional UTI row uses dash form: `15%-Rs 1.50 Per Unit`. It is stored as percentage `15` and
annotated INR-per-unit amount `1.50`. This is a lexical source normalization only, not publication
of a canonical payment cash flow.

Distribution synchronization has an opt-in row quarantine policy. It never applies to invalid JSON
or envelope structure. Under that policy, valid rows are published, invalid row JSON and errors are
persisted in `distribution_parse_issues`, and the run is labelled `completed_with_issues` rather
than cleanly completed. Strict fail-fast behavior remains the default.

The distribution selection endpoint uses a family-level `scheme_id`, while each returned row also
contains option-level `SD_ID`. Exact `SD_ID` matches against AMFI NAV scheme codes are auditable;
missing matches remain unresolved. Names are never used as durable identity keys.

The completed source survey found 21 `SD_ID` values across 103 economic source rows that have no
exact NAV scheme code. Each now has an append-only `source_only` review tied to its immutable AMFI
distribution artifact. This status records source occurrence only; it is not an alias or
economic-identity mapping. The qualifier-aware refresh retains a second normalized source-row
version, so the queryable table and acceptance report show 206 versions for those same 103 rows.
The survey likewise found 225 zero-valued scalar rows across 114 option IDs, represented by 450
source-row versions after refresh. They remain ineligible for canonical payout cash flows without
additional official evidence.

The normalization gate publishes only exact-ID, positive, amount-valued rows with explicit
dividend/IDCW label evidence into revisioned canonical `idcw_cash` events. Each canonical revision
links to its exact immutable source row. The AMFI date remains `record_date`; payment date,
announcement availability, reinvestment behavior, and investor cash flows are not inferred.

Official HDFC notice publication requires two independently captured content-addressed artifacts:
the IDCW declaration and the HDFC scheme summary containing AMFI codes. `unknown` local plan
metadata may be refined by that exact official mapping; an explicit local direct/regular conflict
fails. A notice amount that differs from an existing current canonical revision also fails without
changing the canonical value. Equal multi-source observations link to the same revision. The first
proof notice publishes INR 0.250 per unit on 2026-02-25 for AMFI codes `100120` and `118969`.

The current NAV feed contains some stale historical bonus options with a literal `0.0000` value.
Phase 1 preserves those source rows so the artifact remains complete. Phase 2 must classify them as
data-quality errors and must not publish them as usable NAV observations or convert them into returns.
The feed also currently contains at least one non-standard identifier in an ISIN column. Phase 1
preserves the source string; Phase 2 must validate identifier syntax and quarantine invalid values.

`MF_ID` and `scheme_id` are source query identifiers. They are provenance fields, not durable
internal economic-instrument identifiers. Phase 2 will reconcile AMFI scheme codes, ISINs, and
lifecycle evidence before publishing canonical entities.
