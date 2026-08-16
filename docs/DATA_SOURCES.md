# Official AMFI source registry

Verified against the official AMFI website on 16 August 2026. Source formats and routes are treated
as versioned external contracts and are validated before any future normalized publication.

| Source | Official endpoint | Captured format | Parser policy |
| --- | --- | --- | --- |
| Complete latest NAV | `https://portal.amfiindia.com/spages/NAVAll.txt` | Semicolon-delimited text | Exact header/field count; non-negative decimal source value; explicit NAV date; duplicate scheme/date rejection |
| Historical NAV | `https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx` | Semicolon-delimited text | Maximum 90-day request; exact eight-field format; same value/date/duplicate checks |
| Scheme list by mutual fund | `https://www.amfiindia.com/api/populate-scheme` | JSON | Required exact identifier/name shape; numeric identifiers; duplicate rejection |
| Scheme details | `https://www.amfiindia.com/api/scheme-details` | JSON | Required identity, type, category, and launch-date fields |
| Scheme distributions | `https://www.amfiindia.com/api/scheme-dividend` | JSON | Required NAV-name, record-date, and source-value fields; source value is not normalized in Phase 1 |

The AMFI distribution page states that records through 06 April 2009 are percentages and later
records are amounts. Phase 1 therefore preserves the source value without pretending the units are
uniform. Unit-aware normalization is a Phase 2 responsibility.

The current NAV feed contains some stale historical bonus options with a literal `0.0000` value.
Phase 1 preserves those source rows so the artifact remains complete. Phase 2 must classify them as
data-quality errors and must not publish them as usable NAV observations or convert them into returns.
The feed also currently contains at least one non-standard identifier in an ISIN column. Phase 1
preserves the source string; Phase 2 must validate identifier syntax and quarantine invalid values.

`MF_ID` and `scheme_id` are source query identifiers. They are provenance fields, not durable
internal economic-instrument identifiers. Phase 2 will reconcile AMFI scheme codes, ISINs, and
lifecycle evidence before publishing canonical entities.
