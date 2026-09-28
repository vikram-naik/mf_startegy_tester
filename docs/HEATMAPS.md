# Fund and market heatmaps

## Scope and API

The top-level **Heatmaps** view compares three explicitly separate return universes:

- `funds`: current-catalogue AMFI Growth scheme options, grouped by the same local classification
  aliases used by the screener;
- `benchmarks`: locally stored official Nifty gross total-return (TRI) and net total-return (NTR)
  series; and
- `indices`: locally stored official Nifty price-index series.

It also provides an `IDCW payouts` view backed directly by the IDCW screener API. That view is
classification-specific, fixed to a trailing 12-month window, colors each scheme-option tile by
payout yield, and orders tiles by the screener's equal-weight yield-and-frequency percentile score.
It is intentionally not part of the return heatmap endpoint because payout yield is neither NAV
return nor total return.

The typed endpoint is:

```text
GET /api/v1/data/heatmap?universe=funds&period=1m&plan_type=direct
```

Supported trailing periods are `1m`, `3m`, `6m`, `1y`, `3y`, `5y`, and `10y`. Supported rolling
periods are `rolling_1y`, `rolling_3y`, `rolling_5y`, and `rolling_10y`. The default requested as-of
date is today's `Asia/Kolkata` date. Every tile exposes its actual stored endpoint and staleness;
the requested period is not silently shifted backward to the newest local row. The API accepts an
explicit historical `as_of` for reproducibility and rejects future dates.

## Fund tile calculation

Only Growth options are candidates. Direct and Regular plans are selected separately and are never
collapsed. For every eligible scheme option, trailing returns reuse the screener's exact endpoint
rule: the end NAV must be no more than seven calendar days before the requested as-of date, and the
start NAV is the first valid NAV on or after the calendar target within seven days. Missing NAVs are
not zero, interpolated, or forward-filled.

For an option with start/end values \(NAV_0\), \(NAV_T\), and actual elapsed days \(D\):

\[
R = NAV_T / NAV_0 - 1
\]

Periods below one year display absolute return. Periods of at least one year display actual/365
CAGR, while the unrounded total return remains part of the underlying calculation. A classification
tile is the median return of its eligible constituent options. The response exposes constituent
minimum/maximum, candidate/eligible/excluded counts, actual start/end date ranges, staleness, and
the alias mapping version.

For rolling fund tiles, each scheme contributes its median annualized rolling return. Rolling end
observations are the last valid NAV observed in each calendar month through the requested as-of
date (including the current partial month); starts are the first valid NAV on or after the
corresponding calendar anniversary within seven calendar days. The tile is then the median of
those per-scheme medians, so long-lived schemes do not receive more category weight merely because
they contain more observations. `sample_count` still reports all accepted overlapping
scheme-month periods.

## Official-series tiles

Each official series is its own tile; there is no cross-series aggregation. Price, TRI, and NTR are
never substituted for one another. Trailing series use the same target, tolerance, elapsed-day, and
annualization conventions as funds. Rolling official-series tiles use every valid daily official
observation and the same seven-day anniversary rule. Immutable duplicate observations for one date
are resolved to the latest observed revision, matching the standalone benchmark API.

## Interpretation and limitations

The colors describe past returns, not investment recommendations or a trade signal. Fund results
use the latest observed metadata/current catalogue and therefore are not a survivorship-free
historical-universe study. NAV date is not proof of when a value became knowable. Fund Growth NAVs
already reflect scheme expenses; IDCW options are excluded. TRI reinvests gross distributions, NTR
uses the provider's withholding assumptions, and price indices exclude distributions.

On the retained 33.7-million-row local NAV dataset, the measured Direct Growth heatmap took about
2.8 seconds for a trailing period and 13.1 seconds for a one-year monthly rolling view. The six
price-index rolling tiles and twelve TRI/NTR trailing tiles together took about 1.5 seconds. These
are local diagnostic measurements, not latency guarantees; no cache or derived summary table was
added without a demonstrated need and explicit invalidation design.
