# Benchmark acquisition

## Scope and semantics

The benchmark lane stores four distinct series types:

- Nifty price index;
- Nifty gross total-return index (TRI);
- Nifty net total-return index (NTR); and
- exchange-traded ETF prices from NSE and BSE.

Price, gross-TRI, and net-TRI observations are separate instruments. NSE and BSE ETF observations
remain separate exchange observations even when the official ISIN proves that both listings refer
to the same fund unit. No index reinvestment assumption or exchange-price alignment is inferred.

Exchange numeric security IDs are not treated as immutable ETF identities. When an official dated
file associates the same exchange ID with a different ISIN, each ISIN remains a separate instrument
and a `security_identity_change` warning is recorded. Price histories are not joined or split-
adjusted until an explicit corporate-action conversion and ratio have been normalized.

Every response is retained in the content-addressed raw artifact store. Normalized observations are
immutable: a changed official value creates another signed revision and an
`observation_revision` issue. Checkpoints make `full` runs restart-safe; `refresh` re-observes the
requested range.

Early official Nifty price-index history may publish a valid close with the exact `-` marker for
open, high, and low. Those three fields are retained as unavailable (`null`); they are never copied
from close or otherwise inferred. Close remains mandatory and strictly positive.
Similarly, an official total-return row may contain a valid gross TRI with `NTR_Value` set to the
exact `-` marker. The gross observation is retained and NTR is left unavailable; GTR is never used
as a substitute for NTR.
If the official source publishes internally impossible OHLC bounds but a valid positive close, the
raw row remains immutable, normalized OHLC is omitted as an untrustworthy tuple, and close is
retained with a provenance-linked `invalid_ohlc` warning. No source value is corrected or inferred.
Exchange-wide bhavcopies are parsed before ETF filtering. An invalid OHLC tuple on an unrelated
security does not block ETF extraction or create an ETF data-quality issue. If the affected row is
an identified ETF, its close is retained and the `invalid_ohlc` warning is linked to that ingestion.
Legacy BSE files may encode an invalid OHLC component as zero while retaining a positive close
(observed for non-ETF scrip `511672` on 2016-03-29). Such a tuple is handled by the same audited
invalid-OHLC path; zero is never accepted or substituted as a normalized open, high, or low.
Legacy BSE ZIPs are treated as bundles rather than assumed to contain one file. The parser selects
exactly the root-level `EQDDMMYY.CSV` whose embedded date matches the requested date. Other members
remain only in the immutable raw artifact and are never executed or ingested. Observed extras
include a 58-byte jZip tool marker on 2014-03-26 and an unrelated, wrong-date debt-market DBF on
2011-10-13. A missing or duplicate date-matching equity CSV, nested target, or oversized target
remains a structural error.
Legacy BSE also published a whitespace-only descriptive `SC_NAME` for non-ETF scrip `526225` on
2013-10-31. The stable numeric security ID and prices are retained while the parsed name remains
explicitly unavailable; no name is fabricated. Other source formats still require non-empty names,
and normalized ETF listings require a non-empty official symbol.
Some legacy BSE debt rows publish an exact empty `PREVCLOSE` while retaining valid OHLC and last
prices (observed for scrips `946003` and `961715` on 2012-01-25). Previous close is retained as
unavailable and is never replaced with close, last, or zero. Non-empty malformed and negative
previous-close values remain fatal, and other exchange formats retain their stricter contracts.
The UDiFF `LastPric` field is supplementary to the canonical daily close and may be exactly empty
for some official debt rows. Exact emptiness is retained as unavailable (`null`); close is never
copied into last price. Other non-decimal markers remain parsing errors.
Legacy NSE cash-market bhavcopies may use the exact `DUMMY` marker when an unrelated security has
no published ISIN (observed for `ABFRLPP1`, series `E1`, on 2021-02-16). That marker is normalized
to an unavailable ISIN. NSE also published the exact truncated token `INE` for unrelated symbol
`ICICI`, series `M1`, on 2013-11-06; it receives the same unavailable treatment without inferring
the missing identifier. All other malformed ISIN values remain fatal. Because ETF observations
require an official ISIN, a roster-selected ETF carrying either token would be reported as
`identity_unresolved` and would not be persisted.
NSE has also published a legacy archive with its single CSV under a redundant path whose directory
and filename are identical (observed as
`cm13JUL2020bhav.csv/cm13JUL2020bhav.csv`). The parser accepts only that date-matched form or the
usual root-level filename. Arbitrary subdirectories, traversal paths, backslashes, mismatched
dates, extra members, and non-CSV members remain structural errors.
Some observed files omit the usual trailing empty CSV column. Column layout and timestamp layout
vary independently: both the four-digit `10-JUL-2017` form and two-digit `13-Jul-20` form have been
observed with the compact header. The parser accepts only those two explicit date shapes under the
usual or compact field set; other field sets, date shapes, and date mismatches still fail loudly.
Older NSE cash-market bhavcopies can predate both the `TOTALTRADES` and `ISIN` columns. The reduced
schema is accepted only before the observed transition: the 2011-06-21 archive lacks both columns,
while 2011-06-22 includes them. Otherwise complete price and activity rows retain trade count and
ISIN as explicitly unavailable. The dated ETF report still supplies point-in-time membership, but
the service does not join those prices to a current or future identity: a matched ETF is reported
as `identity_unresolved` and omitted until an official ISIN-backed identity rule is available.
Some legacy BSE rows publish a zero close despite positive OHLC, last, and traded values (observed
for multiple gold ETFs on 2018-04-18). Zero is retained only as an audited source marker and is not
substituted with last, previous close, or another price. A matched ETF receives a provenance-linked
`invalid_close` error and no observation for that date; other valid ETF rows from the batch continue
to ingestion. Negative and non-decimal closes remain structural parsing errors.

## Official sources

Nifty history uses the official index mapping and historical price/total-return APIs. Requests are
split into at most 365 inclusive calendar days because that is the observed official contract.

ETF classification and prices use:

- the current NSE ETF security master;
- NSE's date-specific ETF file inside each official daily press-report ZIP;
- NSE cash-market bhavcopies;
- BSE's current ETF market-watch roster; and
- BSE cash-market bhavcopies.

NSE requests carry browser-like `User-Agent`, `Accept`, language, cache, fetch, and referrer
headers. Requests to `www.nseindia.com` first establish an NSE cookie session. Archive downloads
remain on the official `nsearchives.nseindia.com` host.

The dated NSE ETF membership CSV is available from the observed 2010-03-08 press archive. Valid
date-matched press archives immediately before that boundary (verified for 2010-03-03 through
2010-03-05) contain the core `PrDDMMYY.csv` report but no ETF member. Those dates are retained and
checkpointed as `empty_source_period`; ETF membership or ISIN is not inferred from current data.
A missing ETF member on or after 2010-03-08 remains a structural parsing error.

The BSE ETF endpoint has returned two exact official schemas: a broad market-watch roster and a
smaller categorized subset. Both are parsed explicitly. A categorized response creates a
`partial_bse_roster` warning; it is never reported as complete BSE-only coverage.

## Long runs

Run these from separate terminal sessions, one at a time. Both wrappers use the shared acquisition
lock, apply migrations, and write timestamped JSON under `data/benchmark-reports/`.

```bash
./scripts/sync_nifty_benchmarks.sh
./scripts/sync_etf_prices.sh
```

Useful bounded runs:

```bash
MFST_NIFTY_START_DATE=2020-01-01 \
MFST_NIFTY_END_DATE=2026-08-21 \
MFST_NIFTY_INDICES='Nifty 50,Nifty 500' \
./scripts/sync_nifty_benchmarks.sh

MFST_ETF_START_DATE=2024-07-08 \
MFST_ETF_END_DATE=2026-08-21 \
MFST_ETF_EXCHANGES=NSE,BSE \
./scripts/sync_etf_prices.sh
```

Do not background the commands inside the wrapper. If interrupted, rerun the same command in
`full` mode; committed dates/windows are skipped. An HTTP 404 is retained as an informational
source failure but is deliberately not checkpointed, because a current-day file may simply not
have been published yet. Every calendar day is checked so a special weekend trading session is not
silently omitted.

For an absent modern or legacy BSE daily file, the official file URL may return BSE's exact generic
homepage with HTTP 200 instead of a 404. That observed homepage signature is retained as an
artifact and classified as `empty_trading_day`; unrecognized HTML and malformed CSV remain fatal
structural errors. Like a 404, this response is not checkpointed, so a later publication can still
be found.

For an unpublished NSE press-report date, the official URL may return a valid ZIP belonging to a
different date. A ZIP containing exactly one validly dated, nonmatching ETF member is retained as
an artifact and classified as `empty_trading_day`; its rows are never ingested under the requested
date, and no checkpoint is written. Missing, duplicate, or malformed ETF members remain fatal.
NSE press-report archives normally place the dated ETF CSV at the ZIP root. Some older archives
wrap the entire report in an exact `nuprDDMMYY/` directory (observed on 2013-01-10). The parser
accepts `nuprDDMMYY/etfDDMMYY.csv` only when both embedded dates equal the requested date. Other
directories, mismatched embedded dates, unsafe paths, or multiple matching ETF members remain
fatal.

After a run, report the printed sync and coverage report paths. Independently regenerate coverage
with:

```bash
backend/.venv/bin/mfst benchmark-report
```

## Coverage limitations

- NSE's daily ETF report supplies point-in-time ETF membership and avoids using only today's NSE
  roster for historical classification. That evidence starts on 2010-03-08, so earlier NSE ETF
  prices are not normalized without an additional official historical identity source.
- BSE does not expose a security-level historical ETF roster in the inspected archive API. Older
  BSE prices identified through a later official ISIN/scrip-code mapping are therefore marked
  `provisional_current_mapping` and counted separately.
- The BSE historical archive endpoint inspected during implementation contains only aggregate ETF
  market totals; it is not accepted as instrument-level identity evidence.
- A `completed` request range means every source window/date was handled. It does not prove that
  every benchmark existed for the full requested range.
- Index price return and total return are not interchangeable. Backtests must select the series
  economically appropriate to the strategy and disclose the choice.

The data-acquisition phase is complete only after both long runs have acceptance reports with the
expected date ranges, failed-request counts have been investigated, and provisional/partial BSE
coverage remains visible in dataset metadata.
