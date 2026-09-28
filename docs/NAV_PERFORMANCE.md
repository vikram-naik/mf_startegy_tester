# NAV-only scheme performance

**Calculation version:** initial implementation, 2026-08-17

## Scope

The Data view exposes performance for one AMFI scheme option at a time. Direct/Regular and
Growth/IDCW/Bonus options are distinct instruments and are never combined. The endpoint is:

```text
GET /api/v1/data/schemes/{amfi_scheme_code}/performance
```

Only current NAV revisions with `quality_status = valid` are used. Results are based on the NAV
series itself and are labelled `nav_only`.

For Growth options, NAV change generally represents the option's accumulating economic series. For
IDCW options, NAV change excludes the separate distribution cash flows and must not be interpreted
as investor total return. The API explicitly returns `distribution_treatment = excluded`.

## Since-inception return

For first valid NAV \(NAV_0\), latest valid NAV \(NAV_T\), and actual elapsed calendar days \(D\):

\[
R_{total} = \frac{NAV_T}{NAV_0} - 1
\]

\[
R_{annualized} = \left(\frac{NAV_T}{NAV_0}\right)^{365/D} - 1
\]

The day-count convention is `Actual/365`. A one-observation series has a total return of zero and
no annualized return.

## Rolling returns

The UI reports 1-, 3-, 5-, and 10-year rolling annualized NAV returns. For every valid end-date NAV:

1. Calculate its calendar anniversary target for the requested number of years. A 29-Feb target
   becomes 28-Feb when the target year is not a leap year.
2. Select the first valid NAV on or after that target.
3. Accept the pair only when the selected start NAV is no more than seven calendar days after the
   target. A larger gap is missing data, not a holiday adjustment, and produces no sample.
4. Annualize using the actual elapsed days and the `Actual/365` formula above.

No NAV is forward-filled or interpolated. Samples use overlapping daily endpoints; consequently,
sample count is not a count of independent periods. Each window reports latest, minimum, median,
mean, maximum, percentage of positive samples, and sample count.

The scheme summary cards present the latest 3-, 5-, and 10-year observations as point-to-point
NAV CAGR beside since-inception NAV CAGR. These values are the `latest` members of the matching
rolling-return windows, so they use exactly the same anniversary alignment, Actual/365
annualization, and missing-data rule. A card shows insufficient history rather than substituting a
shorter period.

## Maximum drawdown

For each NAV date \(t\):

\[
Peak_t = \max_{u \le t}(NAV_u)
\]

\[
DD_t = \frac{NAV_t}{Peak_t} - 1
\]

The report returns the minimum drawdown and its retained peak/trough NAV dates. It is a NAV-series
drawdown; IDCW cash payouts remain excluded.

## Limitations

- Results use the current normalized revision for each NAV date, not a frozen reproducible dataset
  snapshot.
- NAV date does not establish when the value became available to an investor. These results are
  descriptive and must not be used as point-in-time signals without an availability convention.
- No benchmark, risk-free rate, volatility, fees, taxes, exit loads, or investor cash flows are
  included.
- Percentage display is rounded in the browser only; backend calculations retain Decimal precision.

Canonical IDCW events can be inspected independently through:

```text
GET /api/v1/data/schemes/{amfi_scheme_code}/distributions
```

That endpoint includes immutable revisions and exact AMFI source artifact/parser provenance. It
does not convert record-date events into payment-date portfolio cash flows.

The separate trailing payout-yield ranking and its NAV-CAGR context are documented in
[`IDCW_SCREENER.md`](IDCW_SCREENER.md).
