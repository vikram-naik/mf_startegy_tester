# Trailing 12-month IDCW payout-yield screener

## Scope

The fund screener supports an explicit `idcw` option universe alongside the separate Growth
universe. IDCW screening is fixed to 12 months and keeps Direct and Regular plans separate. Only
current metadata identified as IDCW payout (`isin_payout_or_growth` is present) is eligible;
reinvestment-only identity is not silently treated as a cash-payout option.

The API uses the existing endpoints:

```text
GET /api/v1/data/classifications?option_type=idcw&horizon=1y
GET /api/v1/data/screener?classification_id=...&option_type=idcw&horizon=1y
```

Any other horizon with `option_type=idcw` returns HTTP 422. Growth behavior remains the default for
backward compatibility.

## Yield calculation

For requested as-of date (T), its calendar anniversary (T_0), current canonical distribution
amounts (P_i), and the accepted end NAV (NAV_T):

\[
PayoutYield = \frac{\sum_{T_0 < record\_date_i \le T} P_i}{NAV_T} \times 100
\]

The start boundary is exclusive and the as-of boundary is inclusive. Only current canonical
distribution revisions are summed. Decimal values remain Decimal through the backend calculation.
The endpoint returns the summed INR-per-unit amount, event count, latest record date, and yield for
each row.

A payout yield is not total return and is not annualized. It is a trailing
cash-declaration-to-endpoint-NAV ratio.

## Yield-and-frequency ranking

Frequency is the count of current canonical payout declarations whose record dates fall inside the
same trailing 12-month window. The ranking does not infer payment dates, count non-canonical source
rows, or convert missing payout evidence to zero.

Within the selected classification and filters, each eligible option receives two competition
ranks: descending payout yield and descending payout-event count. Tied values receive the same
component rank. Each component rank is converted to a 0-100 percentile score and the final IDCW
preference score gives them equal weight:

\[
ComponentPercentile = 100 \times \frac{N - ComponentRank}{N - 1}
\]

For a one-option result set, both component percentiles are defined as 100. Then:

\[
Score = 0.5 \times YieldPercentile + 0.5 \times FrequencyPercentile
\]

The final order is descending score, then descending payout yield, descending payout-event count,
and AMFI scheme code. This rank-based score limits the influence of extreme yield magnitudes. It is
a user-preference score, not a return, risk metric, forecast, or investment recommendation. The API
returns the score and both component ranks so the result is auditable.

## IDCW heatmap

The Heatmaps workspace includes an `IDCW payouts` view. It selects one AMFI classification and plan
at a time, renders payout yield as color intensity, and preserves the exact screener ordering and
ranking evidence. Up to 100 options are rendered; the visible count and total are shown so a
truncated classification is not presented as complete.

## NAV CAGR

The same endpoint rules used by the Growth screener select the first valid NAV from the calendar
anniversary through the configured tolerance and the latest valid NAV no later than as-of. For
actual elapsed days (D):

\[
NAVCAGR = \left(\frac{NAV_T}{NAV_0}\right)^{365/D} - 1
\]

NAV CAGR is displayed as context and does not affect IDCW rank. It excludes cash distributions, so
it must not be interpreted as an IDCW investor's total return.

## Exclusions and limitations

- A stale end NAV or missing acceptable start NAV remains an explicit exclusion.
- An otherwise NAV-eligible option with no current canonical declaration inside the window is
  excluded as `no_payout_events`; missing payout evidence is never converted to zero.
- Distribution dates are source record dates. They are not inferred payment dates or evidence of
  when cash became available.
- Canonical events use the accepted source precedence, but `events_present` does not certify that
  every historical payout was acquired. Source coverage limitations therefore remain material to
  both yield and frequency comparisons.
- Current catalog metadata defines the universe. The result is not survivorship-free and is not an
  investment recommendation.
