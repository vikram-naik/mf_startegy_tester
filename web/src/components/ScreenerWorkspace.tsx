import { useEffect, useMemo, useState } from "react";

import {
  compareFunds,
  getBenchmarkPerformance,
  listBenchmarkSeries,
  listClassifications,
  listFundHouses,
  screenFunds,
  type BenchmarkPerformance,
  type BenchmarkSeries,
  type FundComparison,
  type FundHouse,
  type FundScreener,
  type SchemeProductType,
  type SchemeStructure,
  type ScreenerClassification,
  type ScreenerFund,
  type ScreenerHorizon,
  type ScreenerOptionType,
} from "../api/client";
import {
  availableSchemeProductTypes,
  availableSchemeStructures,
  classificationsForProductType,
  classificationsForStructure,
  schemeProductTypeLabels,
  schemeStructureLabels,
} from "../model/classificationFilters";
import { NavComparisonChart } from "./NavComparisonChart";

const PAGE_SIZE = 25;

function message(error: unknown): string {
  return error instanceof Error ? error.message : "Unable to load screener results.";
}

function percent(value: string | null): string {
  return value === null ? "—" : `${Number(value).toFixed(2)}%`;
}

function nav(value: string): string {
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 4 });
}

function decimal(value: string | null): string {
  return value === null
    ? "—"
    : Number(value).toLocaleString(undefined, { maximumFractionDigits: 4 });
}

function score(value: string | null): string {
  return value === null ? "—" : Number(value).toFixed(1);
}

function exclusionReason(
  reason: "stale_endpoint" | "insufficient_history" | "no_payout_events",
): string {
  if (reason === "stale_endpoint") return "Stale endpoint";
  if (reason === "no_payout_events") return "No payout declarations";
  return "Insufficient history";
}

function benchmarkBasis(value: BenchmarkSeries["return_basis"]): string {
  if (value === "gross_total_return") return "Gross total return (TRI)";
  if (value === "net_total_return") return "Net total return (NTR)";
  return "Price index";
}

export function ScreenerWorkspace() {
  const [fundHouses, setFundHouses] = useState<FundHouse[]>([]);
  const [classifications, setClassifications] = useState<ScreenerClassification[]>([]);
  const [productType, setProductType] = useState<SchemeProductType>("mutual_fund");
  const [schemeStructure, setSchemeStructure] = useState<SchemeStructure>("open_ended");
  const [classification, setClassification] = useState("");
  const [planType, setPlanType] = useState<"direct" | "regular">("direct");
  const [optionType, setOptionType] = useState<ScreenerOptionType>("growth");
  const [horizon, setHorizon] = useState<ScreenerHorizon>("1y");
  const [fundHouse, setFundHouse] = useState("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [exclusionOffset, setExclusionOffset] = useState(0);
  const [results, setResults] = useState<FundScreener | null>(null);
  const [selected, setSelected] = useState<ScreenerFund[]>([]);
  const [comparison, setComparison] = useState<FundComparison | null>(null);
  const [benchmarks, setBenchmarks] = useState<BenchmarkSeries[]>([]);
  const [benchmarkListStatus, setBenchmarkListStatus] = useState<
    "loading" | "ready" | "error"
  >("loading");
  const [benchmarkId, setBenchmarkId] = useState("");
  const [benchmark, setBenchmark] = useState<BenchmarkPerformance | null>(null);
  const [benchmarkError, setBenchmarkError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listFundHouses().then(setFundHouses).catch((reason: unknown) => setError(message(reason)));
    listBenchmarkSeries()
      .then((items) => {
        setBenchmarks(items);
        setBenchmarkListStatus("ready");
      })
      .catch((reason: unknown) => {
        setBenchmarkListStatus("error");
        setBenchmarkError(message(reason));
      });
  }, []);

  useEffect(() => {
    let current = true;
    listClassifications(planType, optionType, horizon, fundHouse)
      .then((items) => {
        if (!current) return;
        setClassifications(items);
        setClassification((value) =>
          items.some((item) => item.classification_id === value) ? value : "",
        );
      })
      .catch((reason: unknown) => current && setError(message(reason)));
    return () => { current = false; };
  }, [planType, optionType, horizon, fundHouse]);

  useEffect(() => {
    setOffset(0);
    setExclusionOffset(0);
    setSelected([]);
    setComparison(null);
  }, [classification, productType, planType, optionType, horizon, fundHouse]);

  useEffect(() => {
    const productTypes = availableSchemeProductTypes(classifications);
    if (!productTypes.includes(productType)) {
      setProductType(productTypes[0] ?? "mutual_fund");
      return;
    }
    const productClassifications = classificationsForProductType(classifications, productType);
    const structures = availableSchemeStructures(productClassifications);
    if (!structures.includes(schemeStructure)) {
      setSchemeStructure(structures[0] ?? "open_ended");
    }
    if (
      classification
      && !productClassifications.some((item) => item.classification_id === classification)
    ) {
      setClassification("");
    }
  }, [classification, classifications, productType, schemeStructure]);

  useEffect(() => {
    if (!classification) {
      setResults(null);
      return;
    }
    let current = true;
    const timer = window.setTimeout(() => {
      setIsLoading(true);
      setError(null);
      screenFunds({
        classificationId: classification,
        horizon,
        planType,
        optionType,
        fundHouse,
        search,
        limit: PAGE_SIZE,
        offset,
        exclusionLimit: PAGE_SIZE,
        exclusionOffset,
      })
        .then((loaded) => current && setResults(loaded))
        .catch((reason: unknown) => current && setError(message(reason)))
        .finally(() => current && setIsLoading(false));
    }, 180);
    return () => { current = false; window.clearTimeout(timer); };
  }, [classification, planType, optionType, horizon, fundHouse, search, offset, exclusionOffset]);

  useEffect(() => {
    if (selected.length === 0 || results === null) {
      setComparison(null);
      return;
    }
    let current = true;
    compareFunds(
      selected.map((item) => item.amfi_scheme_code),
      results.target_start_date,
      results.as_of_date,
    )
      .then((loaded) => current && setComparison(loaded))
      .catch((reason: unknown) => current && setError(message(reason)));
    return () => { current = false; };
  }, [selected, results]);

  useEffect(() => {
    if (!benchmarkId || results === null) {
      setBenchmark(null);
      return;
    }
    let current = true;
    setBenchmark(null);
    setBenchmarkError(null);
    getBenchmarkPerformance(
      benchmarkId,
      results.horizon,
      results.as_of_date,
      results.endpoint_tolerance_days,
    )
      .then((loaded) => current && setBenchmark(loaded))
      .catch((reason: unknown) => current && setBenchmarkError(message(reason)));
    return () => { current = false; };
  }, [benchmarkId, results]);

  const selectedCodes = useMemo(
    () => new Set(selected.map((item) => item.amfi_scheme_code)),
    [selected],
  );
  const productTypes = useMemo(
    () => availableSchemeProductTypes(classifications),
    [classifications],
  );
  const productClassifications = useMemo(
    () => classificationsForProductType(classifications, productType),
    [classifications, productType],
  );
  const schemeStructures = useMemo(
    () => availableSchemeStructures(productClassifications),
    [productClassifications],
  );
  const classificationOptions = useMemo(
    () => classificationsForStructure(productClassifications, schemeStructure),
    [productClassifications, schemeStructure],
  );
  const excludedTotal = results
    ? results.excluded_insufficient_history
      + results.excluded_stale_endpoint
      + results.excluded_no_payout_events
    : 0;

  const changeOptionType = (next: ScreenerOptionType) => {
    setClassification("");
    setOptionType(next);
    if (next === "idcw") setHorizon("1y");
  };

  const changeProductType = (next: SchemeProductType) => {
    setProductType(next);
    setClassification("");
    const structures = availableSchemeStructures(
      classificationsForProductType(classifications, next),
    );
    setSchemeStructure(structures[0] ?? "open_ended");
  };

  const changeSchemeStructure = (next: SchemeStructure) => {
    setSchemeStructure(next);
    const selectedClassification = classifications.find(
      (item) => item.classification_id === classification,
    );
    if (selectedClassification?.structure_type !== next) setClassification("");
  };

  const toggleComparison = (fund: ScreenerFund) => {
    if (selectedCodes.has(fund.amfi_scheme_code)) {
      setSelected(selected.filter((item) => item.amfi_scheme_code !== fund.amfi_scheme_code));
    } else if (selected.length < 5) {
      setSelected([...selected, fund]);
    }
  };

  return (
    <section className="screener-workspace">
      <div className="page-heading">
        <div>
          <span className="eyebrow">
            {optionType === "idcw" ? "Comparable AMFI IDCW payout options" : "Comparable AMFI Growth options"}
          </span>
          <h1>Fund screener</h1>
        </div>
        <p>{optionType === "idcw"
          ? "Rank IDCW funds across AMCs by an equal-weight blend of trailing 12-month payout yield and payout frequency, with NAV CAGR shown separately."
          : "Rank funds across AMCs by an exact trailing NAV period, then compare up to five funds from the same classification."}</p>
      </div>

      <div className="screener-card">
        <fieldset className="scheme-structure-filter">
          <legend>Product universe</legend>
          <div className="scheme-structure-options">
            {productTypes.map((candidate) => {
              const eligible = classificationsForProductType(classifications, candidate)
                .reduce((total, item) => total + item.eligible_options, 0);
              return (
                <label key={candidate}>
                  <input
                    type="radio"
                    name="product-type"
                    value={candidate}
                    checked={productType === candidate}
                    aria-label={`${schemeProductTypeLabels[candidate]}, ${eligible} eligible options`}
                    onChange={() => changeProductType(candidate)}
                  />
                  <span>{schemeProductTypeLabels[candidate]}<small>{eligible}</small></span>
                </label>
              );
            })}
          </div>
        </fieldset>
        <fieldset className="scheme-structure-filter">
          <legend>Scheme structure</legend>
          <div className="scheme-structure-options">
            {schemeStructures.map((structure) => {
              const count = classificationsForStructure(productClassifications, structure).length;
              return (
                <label key={structure}>
                  <input
                    type="radio"
                    name="scheme-structure"
                    value={structure}
                    checked={schemeStructure === structure}
                    aria-label={`${schemeStructureLabels[structure]}, ${count} classifications`}
                    onChange={() => changeSchemeStructure(structure)}
                  />
                  <span>
                    {schemeStructureLabels[structure]}
                    <small>{count}</small>
                  </span>
                </label>
              );
            })}
          </div>
        </fieldset>
        <div className="screener-filters">
          <label>
            Option
            <select value={optionType} onChange={(event) => changeOptionType(event.target.value as ScreenerOptionType)}>
              <option value="growth">Growth performance</option>
              <option value="idcw">IDCW payout yield</option>
            </select>
          </label>
          <label>
            AMFI classification
            <select value={classification} onChange={(event) => setClassification(event.target.value)}>
              <option value="">
                Choose a {schemeStructureLabels[schemeStructure].toLowerCase()} classification
              </option>
              {classificationOptions.map((item) => <option key={item.classification_id} value={item.classification_id}>{item.classification} · {item.eligible_options} eligible</option>)}
            </select>
          </label>
          <label>
            Fund house
            <select value={fundHouse} onChange={(event) => setFundHouse(event.target.value)}>
              <option value="">All fund houses</option>
              {fundHouses.filter((item) => item.scheme_options > 0).map((item) => <option key={item.mutual_fund_id} value={item.name}>{item.name}</option>)}
            </select>
          </label>
          <label>
            Plan
            <select value={planType} onChange={(event) => {
              setClassification("");
              setPlanType(event.target.value as "direct" | "regular");
            }}>
              <option value="direct">Direct</option>
              <option value="regular">Regular</option>
            </select>
          </label>
          <label>
            Performance period
            <select value={horizon} disabled={optionType === "idcw"} onChange={(event) => {
              setClassification("");
              setHorizon(event.target.value as ScreenerHorizon);
            }}>
              <option value="1m">1 month</option><option value="3m">3 months</option><option value="6m">6 months</option>
              <option value="1y">1 year</option><option value="3y">3 years</option><option value="5y">5 years</option><option value="10y">10 years</option>
            </select>
            {optionType === "idcw" && <small>Fixed at trailing 12 months</small>}
          </label>
          <label>
            Search
            <input type="search" value={search} placeholder="Fund, code, or ISIN" onChange={(event) => { setSearch(event.target.value); setOffset(0); setExclusionOffset(0); }} />
          </label>
        </div>

        <div className="benchmark-selector">
          <label>
            Official benchmark (optional)
            <select
              value={benchmarkId}
              disabled={benchmarkListStatus !== "ready" || benchmarks.length === 0}
              onChange={(event) => setBenchmarkId(event.target.value)}
            >
              <option value="">
                {benchmarkListStatus === "loading"
                  ? "Loading benchmark series…"
                  : benchmarks.length === 0
                    ? "No benchmark series available"
                    : "No benchmark selected"}
              </option>
              {(["gross_total_return", "net_total_return", "price"] as const).map((basis) => (
                <optgroup key={basis} label={benchmarkBasis(basis)}>
                  {benchmarks.filter((item) => item.return_basis === basis).map((item) => (
                    <option key={item.instrument_id} value={item.instrument_id}>
                      {item.display_name} · through {item.latest_observation_date}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </label>
          <p>{benchmarkListStatus === "error" && benchmarkError
            ? benchmarkError
            : "TRI includes gross distributions; NTR includes distributions net of index withholding assumptions; price indices exclude distributions. Selection is explicit and never changes fund rank."}</p>
        </div>

        {!classification ? (
          <div className="screener-empty">
            <strong>Choose a classification to begin</strong>
            <p>Comparing funds inside one approved canonical classification avoids ranking unlike asset classes as though they were equivalent.</p>
          </div>
        ) : error ? <div className="browser-message">{error}</div> : (
          <>
            <div className="screener-summary">
              <div><span>Eligible funds</span><strong>{results?.total.toLocaleString() ?? "—"}</strong></div>
              <div><span>Ranking metric</span><strong>{results?.ranking_metric === "payout_yield_frequency_score" ? "Yield + frequency" : results?.ranking_metric === "annualized_return_pct" ? "CAGR" : "Absolute return"}</strong></div>
              <div><span>As of</span><strong>{results?.as_of_date ?? "—"}</strong></div>
              <div><span>Excluded</span><strong>{results ? excludedTotal : "—"}</strong></div>
            </div>
            {benchmarkId && (
              <div className="benchmark-result">
                {benchmarkError ? <p>{benchmarkError}</p> : benchmark ? (
                  <>
                    <div><span>Reference</span><strong>{benchmark.display_name}</strong><small>{benchmarkBasis(benchmark.return_basis)}</small></div>
                    <div><span>Reference return</span><strong>{percent(benchmark.annualized_return_pct ?? benchmark.total_return_pct)}</strong><small>{benchmark.status === "available" ? (benchmark.annualized_return_pct ? "actual/365 CAGR" : "absolute return") : benchmark.status.replace("_", " ")}</small></div>
                    <div><span>Actual period</span><strong>{benchmark.start_date ?? "—"} to {benchmark.end_date ?? "—"}</strong><small>{benchmark.elapsed_days === null ? benchmark.reason_detail : `${benchmark.elapsed_days} days · ${benchmark.endpoint_staleness_days}d endpoint staleness`}</small></div>
                  </>
                ) : <p>Loading benchmark evidence…</p>}
              </div>
            )}
            <div className={`table-scroll screener-table ${isLoading ? "is-loading" : ""}`}>
              <table>
                <thead><tr><th>Rank</th><th>Fund</th><th>Fund house</th><th>Actual period</th>{optionType === "idcw" ? <><th>Payout yield</th><th>Frequency</th><th>NAV CAGR</th></> : <th>Performance</th>}<th>Latest NAV</th><th>Compare</th></tr></thead>
                <tbody>
                  {results?.items.map((fund) => (
                    <tr key={fund.amfi_scheme_code}>
                      <td className="rank-cell">{fund.rank}</td>
                      <td><strong>{fund.scheme_name}</strong><small>{fund.amfi_scheme_code} · {fund.plan_type} {fund.option_type}</small></td>
                      <td>{fund.fund_house_name}</td>
                      <td>{fund.start_date}<small>to {fund.end_date} · {fund.elapsed_days} days</small></td>
                      {fund.option_type === "idcw" ? <>
                        <td><strong className="positive-return">{percent(fund.payout_yield_pct)}</strong><small>{decimal(fund.payout_amount_per_unit_inr)} INR/unit · yield rank {fund.payout_yield_rank ?? "—"}</small></td>
                        <td><strong>{fund.payout_event_count} declarations</strong><small>frequency rank {fund.payout_frequency_rank ?? "—"} · latest {fund.latest_payout_record_date ?? "—"} · combined score {score(fund.idcw_rank_score)}</small></td>
                        <td><strong>{percent(fund.annualized_return_pct)}</strong><small>NAV-only; payouts excluded</small></td>
                      </> : <td>
                        <><strong className={Number(fund.total_return_pct) >= 0 ? "positive-return" : "negative-return"}>{percent(fund.annualized_return_pct ?? fund.total_return_pct)}</strong><small>{fund.annualized_return_pct ? `CAGR · total ${percent(fund.total_return_pct)}` : "absolute NAV return"}</small></>
                      </td>}
                      <td>{nav(fund.end_nav)}<small>{fund.endpoint_staleness_days === 0 ? "current endpoint" : `${fund.endpoint_staleness_days}d before as-of`}</small></td>
                      <td><button className={selectedCodes.has(fund.amfi_scheme_code) ? "compare-button selected" : "compare-button"} disabled={!selectedCodes.has(fund.amfi_scheme_code) && selected.length >= 5} onClick={() => toggleComparison(fund)}>{selectedCodes.has(fund.amfi_scheme_code) ? "Added" : "Add"}</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {results && results.total > PAGE_SIZE && <div className="pagination-bar"><span>{offset + 1}–{Math.min(offset + PAGE_SIZE, results.total)} of {results.total}</span><div><button disabled={offset === 0 || isLoading} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</button><button disabled={offset + PAGE_SIZE >= results.total || isLoading} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</button></div></div>}
            {results && excludedTotal > 0 && (
              <details className="screener-exclusions">
                <summary>
                  Review {excludedTotal} excluded funds
                  <span>{results.excluded_stale_endpoint} stale · {results.excluded_insufficient_history} insufficient history · {results.excluded_no_payout_events} without payouts</span>
                </summary>
                <div className="table-scroll exclusion-table">
                  <table>
                    <thead><tr><th>Fund</th><th>Fund house</th><th>Reason</th><th>NAV evidence</th></tr></thead>
                    <tbody>
                      {results.excluded_items.map((fund) => (
                        <tr key={fund.amfi_scheme_code}>
                          <td><strong>{fund.scheme_name}</strong><small>{fund.amfi_scheme_code} · {fund.plan_type} {fund.option_type}</small></td>
                          <td>{fund.fund_house_name}</td>
                          <td><strong>{exclusionReason(fund.reason)}</strong><small>{fund.reason_detail}</small></td>
                          <td>
                            <strong>{fund.latest_nav_date ?? "No valid endpoint"}{fund.latest_nav ? ` · ${nav(fund.latest_nav)}` : ""}</strong>
                            <small>First stored NAV {fund.first_nav_date}{fund.endpoint_staleness_days === null ? "" : ` · ${fund.endpoint_staleness_days}d stale`}</small>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {excludedTotal > results.exclusion_limit && (
                  <div className="pagination-bar">
                    <span>{results.exclusion_offset + 1}–{Math.min(results.exclusion_offset + results.exclusion_limit, excludedTotal)} of {excludedTotal}</span>
                    <div>
                      <button disabled={results.exclusion_offset === 0 || isLoading} onClick={() => setExclusionOffset(Math.max(0, results.exclusion_offset - results.exclusion_limit))}>Previous</button>
                      <button disabled={results.exclusion_offset + results.exclusion_limit >= excludedTotal || isLoading} onClick={() => setExclusionOffset(results.exclusion_offset + results.exclusion_limit)}>Next</button>
                    </div>
                  </div>
                )}
              </details>
            )}
          </>
        )}
      </div>

      {comparison && <NavComparisonChart comparison={comparison} />}

      <div className="method-grid">
        <article><span>01</span><h3>Trailing performance</h3><p>{optionType === "idcw" ? "Payout yield is trailing declared INR per unit divided by endpoint NAV. Rank equally weights yield and declaration-frequency percentiles; NAV CAGR is shown separately." : "Absolute return below one year; actual/365 CAGR from one year onward. Exact NAV endpoints are shown."}</p></article>
        <article><span>02</span><h3>Risk context</h3><p>Open a fund in Data for maximum drawdown and rolling-return range, median, mean, and positive-period frequency.</p></article>
        <article><span>03</span><h3>Comparison chart</h3><p>Normalize selected NAVs to 100 for a fair visual comparison, or switch to raw NAV. IDCW record-date events appear as markers when present.</p></article>
      </div>
      <div className="methodology-note">{optionType === "idcw" ? `AMFI IDCW payout-option ranking · ${results?.ranking_method ?? "equal-weight payout-yield and payout-frequency percentile score"} · current canonical declarations with record dates inside the trailing 12-month window · record date is not proof of payment date · payout yield is not total return · NAV CAGR excludes cash distributions` : "AMFI Growth-option NAV ranking · IDCW excluded"} · Direct and Regular kept separate · ETF rows use NAV, not exchange close · benchmarks are standalone references and do not change rank · price, GTR, and NTR kept distinct · no NAV forward-fill · current-universe view, not survivorship-free{results ? ` · classification mapping ${results.classification_mapping_version}` : ""}</div>
    </section>
  );
}
