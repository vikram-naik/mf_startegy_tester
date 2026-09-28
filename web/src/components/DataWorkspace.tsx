import { useEffect, useMemo, useState } from "react";

import {
  compareFunds,
  getDataCoverage,
  getSchemePerformance,
  listFundHouses,
  listIngestionBatches,
  listSchemeCategories,
  listSchemeDistributions,
  listSchemes,
  type DataCoverage,
  type DistributionEventBrowser,
  type FundComparison,
  type FundHouse,
  type IngestionBatch,
  type SchemeBrowser,
  type SchemeBrowserItem,
  type SchemeCategory,
  type SchemePerformance,
} from "../api/client";
import { latestRollingReturn, SUMMARY_CAGR_WINDOWS } from "../model/performance";
import { distributionCoverageMessage } from "../model/distributionCoverage";
import { NavComparisonChart } from "./NavComparisonChart";

const PAGE_SIZE = 50;

function formatBytes(value: number | null): string {
  if (value === null) return "—";
  if (value < 1024) return `${value} B`;
  return `${(value / 1024).toFixed(1)} KiB`;
}

function errorMessage(reason: unknown, fallback: string): string {
  return reason instanceof Error ? reason.message : fallback;
}

function formatPercent(value: string | null): string {
  if (value === null) return "—";
  return `${Number(value).toFixed(2)}%`;
}

function formatNav(value: string): string {
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 4 });
}

function fiveYearChartStart(firstDate: string, latestDate: string): string {
  const latest = new Date(`${latestDate}T00:00:00Z`);
  latest.setUTCFullYear(latest.getUTCFullYear() - 5);
  const trailingStart = latest.toISOString().slice(0, 10);
  return firstDate > trailingStart ? firstDate : trailingStart;
}

export function DataWorkspace() {
  const [batches, setBatches] = useState<IngestionBatch[]>([]);
  const [coverage, setCoverage] = useState<DataCoverage | null>(null);
  const [fundHouses, setFundHouses] = useState<FundHouse[]>([]);
  const [selectedFundHouseId, setSelectedFundHouseId] = useState("");
  const [categories, setCategories] = useState<SchemeCategory[]>([]);
  const [category, setCategory] = useState("");
  const [planType, setPlanType] = useState("");
  const [optionType, setOptionType] = useState("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [schemes, setSchemes] = useState<SchemeBrowser | null>(null);
  const [selectedScheme, setSelectedScheme] = useState<SchemeBrowserItem | null>(null);
  const [performance, setPerformance] = useState<SchemePerformance | null>(null);
  const [distributions, setDistributions] = useState<DistributionEventBrowser | null>(null);
  const [comparison, setComparison] = useState<FundComparison | null>(null);
  const [isLoadingDetails, setIsLoadingDetails] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [isLoadingSchemes, setIsLoadingSchemes] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [browserError, setBrowserError] = useState<string | null>(null);

  const selectedFundHouse = useMemo(
    () => fundHouses.find((item) => item.mutual_fund_id === selectedFundHouseId) ?? null,
    [fundHouses, selectedFundHouseId],
  );

  useEffect(() => {
    Promise.all([listIngestionBatches(), getDataCoverage(), listFundHouses()])
      .then(([loadedBatches, loadedCoverage, loadedFundHouses]) => {
        setBatches(loadedBatches);
        setCoverage(loadedCoverage);
        setFundHouses(loadedFundHouses);
        const firstBrowsable = loadedFundHouses.find((item) => item.scheme_options > 0);
        setSelectedFundHouseId(firstBrowsable?.mutual_fund_id ?? loadedFundHouses[0]?.mutual_fund_id ?? "");
      })
      .catch((reason: unknown) => {
        setError(errorMessage(reason, "Unable to load research data status."));
      });
  }, []);

  useEffect(() => {
    if (!selectedFundHouseId) {
      setCategories([]);
      return;
    }
    let current = true;
    listSchemeCategories(selectedFundHouseId)
      .then((loadedCategories) => {
        if (current) setCategories(loadedCategories);
      })
      .catch((reason: unknown) => {
        if (current) setBrowserError(errorMessage(reason, "Unable to load scheme categories."));
      });
    return () => {
      current = false;
    };
  }, [selectedFundHouseId]);

  useEffect(() => {
    if (!selectedFundHouseId) {
      setSchemes(null);
      return;
    }
    let current = true;
    const timer = window.setTimeout(() => {
      setIsLoadingSchemes(true);
      setBrowserError(null);
      listSchemes({
        fundHouseId: selectedFundHouseId,
        categoryId: category,
        planType,
        optionType,
        search,
        limit: PAGE_SIZE,
        offset,
      })
        .then((loadedSchemes) => {
          if (current) setSchemes(loadedSchemes);
        })
        .catch((reason: unknown) => {
          if (current) setBrowserError(errorMessage(reason, "Unable to load scheme options."));
        })
        .finally(() => {
          if (current) setIsLoadingSchemes(false);
        });
    }, 200);
    return () => {
      current = false;
      window.clearTimeout(timer);
    };
  }, [selectedFundHouseId, category, planType, optionType, search, offset]);

  useEffect(() => {
    if (selectedScheme === null) {
      setPerformance(null);
      setDistributions(null);
      setComparison(null);
      setDetailError(null);
      return;
    }
    let current = true;
    setIsLoadingDetails(true);
    setDetailError(null);
    setPerformance(null);
    setDistributions(null);
    setComparison(null);
    Promise.all([
      getSchemePerformance(selectedScheme.amfi_scheme_code),
      listSchemeDistributions(selectedScheme.amfi_scheme_code),
      compareFunds(
        [selectedScheme.amfi_scheme_code],
        fiveYearChartStart(selectedScheme.first_nav_date, selectedScheme.latest_nav_date),
        selectedScheme.latest_nav_date,
      ),
    ])
      .then(([loadedPerformance, loadedDistributions, loadedComparison]) => {
        if (!current) return;
        setPerformance(loadedPerformance);
        setDistributions(loadedDistributions);
        setComparison(loadedComparison);
      })
      .catch((reason: unknown) => {
        if (current) setDetailError(errorMessage(reason, "Unable to load scheme analytics."));
      })
      .finally(() => {
        if (current) setIsLoadingDetails(false);
      });
    return () => {
      current = false;
    };
  }, [selectedScheme]);

  const resetPage = () => setOffset(0);
  const coverageMessage = distributionCoverageMessage(distributions?.coverage ?? null);

  return (
    <section className="data-workspace">
      <div className="data-intro">
        <div>
          <span className="eyebrow">Immutable evidence store</span>
          <h2>AMFI research data</h2>
        </div>
        <p>
          Browse normalized scheme options while retaining AMFI scheme codes, source
          classifications, NAV dates, and ingestion coverage. A scheme name is descriptive—not its
          durable identity.
        </p>
      </div>

      {error && <div className="message">{error}</div>}

      <div className="stat-grid">
        <div><span>Valid NAV rows</span><strong>{coverage?.valid_nav_rows.toLocaleString() ?? "—"}</strong></div>
        <div><span>Scheme options</span><strong>{coverage?.scheme_options.toLocaleString() ?? "—"}</strong></div>
        <div><span>Fund-house coverage</span><strong>{coverage ? `${coverage.fully_covered_funds}/${coverage.active_funds}` : "—"}</strong></div>
        <div><span>Latest NAV date</span><strong>{coverage?.latest_nav_date ?? "—"}</strong></div>
      </div>

      {coverage?.latest_sync_run && (
        <div className="sync-strip">
          <span className={`batch-status ${coverage.latest_sync_run.status}`}>
            {coverage.latest_sync_run.status}
          </span>
          <strong>{coverage.latest_sync_run.mode} AMFI sync</strong>
          <span>{coverage.latest_sync_run.funds_completed}/{coverage.latest_sync_run.funds_total} fund houses</span>
          <span>{coverage.latest_sync_run.chunks_completed.toLocaleString()} windows</span>
          <span>{coverage.latest_sync_run.rows_received.toLocaleString()} source rows</span>
          <span>{coverage.quarantined_nav_rows.toLocaleString()} quarantined</span>
        </div>
      )}

      <div className="scheme-panel">
        <div className="batch-heading">
          <div>
            <span className="eyebrow">Latest stored scheme snapshot</span>
            <h3>Scheme option browser</h3>
          </div>
          <span className="read-only-badge">Read-only</span>
        </div>

        <div className="scheme-filters">
          <label>
            AMFI fund house
            <select
              value={selectedFundHouseId}
              onChange={(event) => {
                setSelectedFundHouseId(event.target.value);
                setSelectedScheme(null);
                setCategory("");
                resetPage();
              }}
            >
              {fundHouses.length === 0 && <option value="">No fund houses loaded</option>}
              {fundHouses.map((fundHouse) => (
                <option key={fundHouse.mutual_fund_id} value={fundHouse.mutual_fund_id}>
                  {fundHouse.name} · {fundHouse.scheme_options.toLocaleString()}
                </option>
              ))}
            </select>
          </label>
          <label>
            AMFI classification
            <select value={category} onChange={(event) => { setCategory(event.target.value); resetPage(); }}>
              <option value="">All classifications</option>
              {categories.map((item) => (
                <option key={item.classification_id} value={item.classification_id}>
                  {item.classification} · {item.scheme_options.toLocaleString()}
                </option>
              ))}
            </select>
          </label>
          <label>
            Plan
            <select value={planType} onChange={(event) => { setPlanType(event.target.value); resetPage(); }}>
              <option value="">All plans</option>
              <option value="direct">Direct</option>
              <option value="regular">Regular</option>
              <option value="unknown">Unclassified</option>
            </select>
          </label>
          <label>
            Option
            <select value={optionType} onChange={(event) => { setOptionType(event.target.value); resetPage(); }}>
              <option value="">All options</option>
              <option value="growth">Growth</option>
              <option value="idcw">IDCW</option>
              <option value="bonus">Bonus</option>
              <option value="unknown">Unclassified</option>
            </select>
          </label>
          <label className="scheme-search">
            Search name, code, or ISIN
            <input
              type="search"
              value={search}
              placeholder="e.g. flexi cap or 119550"
              onChange={(event) => { setSearch(event.target.value); resetPage(); }}
            />
          </label>
        </div>

        <div className="scheme-context">
          <span>
            {selectedFundHouse
              ? `${selectedFundHouse.scheme_options.toLocaleString()} stored scheme options`
              : "Select a fund house"}
          </span>
          <span>
            Historical coverage: {selectedFundHouse?.fully_covered
              ? `complete through ${selectedFundHouse.completed_through}`
              : selectedFundHouse?.completed_through
                ? `partial through ${selectedFundHouse.completed_through}`
                : "not completed"}
          </span>
          <strong>{schemes ? `${schemes.total.toLocaleString()} matching` : "—"}</strong>
        </div>

        {browserError && <div className="browser-message">{browserError}</div>}
        {isLoadingSchemes && schemes === null ? (
          <div className="batch-empty"><strong>Loading scheme options…</strong></div>
        ) : schemes?.items.length === 0 ? (
          <div className="batch-empty">
            <strong>No scheme options match these filters</strong>
            <p>Try removing a category, plan, option, or search constraint.</p>
          </div>
        ) : (
          <div className={`table-scroll scheme-table ${isLoadingSchemes ? "is-loading" : ""}`}>
            <table>
              <thead>
                <tr><th>Scheme option</th><th>Identity</th><th>Plan / option</th><th>Classification</th><th>NAV history</th><th>Latest NAV</th></tr>
              </thead>
              <tbody>
                {schemes?.items.map((scheme) => (
                  <tr key={scheme.amfi_scheme_code} className={selectedScheme?.amfi_scheme_code === scheme.amfi_scheme_code ? "is-selected" : ""}>
                    <td><button className="scheme-link" type="button" onClick={() => setSelectedScheme(scheme)}>{scheme.scheme_name}</button></td>
                    <td>
                      <code>{scheme.amfi_scheme_code}</code>
                      <small>{scheme.isin_payout_or_growth ?? scheme.isin_reinvestment ?? "No ISIN published"}</small>
                    </td>
                    <td><span className="attribute-pill">{scheme.plan_type}</span><small>{scheme.option_type}</small></td>
                    <td className="classification-cell">{scheme.scheme_classification}</td>
                    <td>{scheme.first_nav_date}<small>to {scheme.latest_nav_date}</small></td>
                    <td><strong>{scheme.latest_nav_value}</strong><small className={scheme.quality_status === "valid" ? "quality-valid" : "quality-error"}>{scheme.quality_status}</small></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {schemes && schemes.total > 0 && (
          <div className="pagination-bar">
            <span>
              {schemes.offset + 1}–{Math.min(schemes.offset + schemes.items.length, schemes.total)} of {schemes.total.toLocaleString()}
            </span>
            <div>
              <button disabled={offset === 0 || isLoadingSchemes} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</button>
              <button disabled={offset + PAGE_SIZE >= schemes.total || isLoadingSchemes} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</button>
            </div>
          </div>
        )}

        {selectedScheme && (
          <section className="scheme-detail" aria-live="polite">
            <div className="scheme-detail-heading">
              <div>
                <span className="eyebrow">NAV analytics and canonical distributions</span>
                <h3>{selectedScheme.scheme_name}</h3>
                <code>{selectedScheme.amfi_scheme_code}</code>
              </div>
              <button type="button" onClick={() => setSelectedScheme(null)}>Close</button>
            </div>

            <div className="calculation-note">
              NAV-only returns · IDCW payouts excluded · Actual/365 annualization · no NAV
              forward-fill. For IDCW options, these figures are NAV change—not total return.
            </div>

            {detailError && <div className="browser-message">{detailError}</div>}
            {isLoadingDetails ? (
              <div className="batch-empty"><strong>Calculating scheme analytics…</strong></div>
            ) : performance ? (
              <>
                {comparison && <NavComparisonChart comparison={comparison} />}
                <div className="stat-grid performance-grid">
                  <div><span>Since-inception NAV CAGR</span><strong>{formatPercent(performance.since_inception.annualized_return_pct)}</strong><small>{performance.since_inception.start_date} to {performance.since_inception.end_date}</small></div>
                  {SUMMARY_CAGR_WINDOWS.map((windowYears) => {
                    const trailingReturn = latestRollingReturn(
                      performance.rolling_returns,
                      windowYears,
                    );
                    return (
                      <div key={windowYears}>
                        <span>{windowYears}-year NAV CAGR</span>
                        <strong>{formatPercent(trailingReturn?.annualized_return_pct ?? null)}</strong>
                        <small>
                          {trailingReturn
                            ? `${trailingReturn.start_date} to ${trailingReturn.end_date}`
                            : "Insufficient valid NAV history"}
                        </small>
                      </div>
                    );
                  })}
                  <div><span>Since-inception NAV return</span><strong>{formatPercent(performance.since_inception.total_return_pct)}</strong><small>{formatNav(performance.since_inception.start_nav)} → {formatNav(performance.since_inception.end_nav)}</small></div>
                  <div><span>Maximum NAV drawdown</span><strong>{formatPercent(performance.drawdown.maximum_drawdown_pct)}</strong><small>{performance.drawdown.peak_date} to {performance.drawdown.trough_date}</small></div>
                  <div><span>Valid observations</span><strong>{performance.observation_count.toLocaleString()}</strong><small>Current stored revisions</small></div>
                </div>

                <div className="detail-section">
                  <h4>Rolling annualized NAV returns</h4>
                  <p>{performance.rolling_start_rule}. Overlapping daily endpoint samples.</p>
                  <div className="table-scroll">
                    <table>
                      <thead><tr><th>Window</th><th>Latest</th><th>Median</th><th>Mean</th><th>Range</th><th>Positive</th><th>Samples</th></tr></thead>
                      <tbody>
                        {performance.rolling_returns.map((item) => (
                          <tr key={item.window_years}>
                            <td><strong>{item.window_years} year{item.window_years === 1 ? "" : "s"}</strong></td>
                            <td>{formatPercent(item.latest?.annualized_return_pct ?? null)}<small>{item.latest ? `${item.latest.start_date} to ${item.latest.end_date}` : "Insufficient history"}</small></td>
                            <td>{formatPercent(item.median_annualized_return_pct)}</td>
                            <td>{formatPercent(item.mean_annualized_return_pct)}</td>
                            <td>{formatPercent(item.minimum_annualized_return_pct)} to {formatPercent(item.maximum_annualized_return_pct)}</td>
                            <td>{formatPercent(item.positive_periods_pct)}</td>
                            <td>{item.sample_count.toLocaleString()}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>

                <div className="detail-section">
                  <h4>Canonical IDCW events</h4>
                  <p>Official AMFI or AMC record dates and INR amounts per unit. Payment dates and portfolio cash flows are not inferred.</p>
                  <div className={`coverage-note ${coverageMessage.tone}`}>
                    <strong>{coverageMessage.label}</strong>
                    <span>{coverageMessage.detail}</span>
                  </div>
                  {distributions?.items.length ? (
                    <div className="table-scroll">
                      <table>
                        <thead><tr><th>Record date</th><th>Current amount/unit</th><th>Revision</th><th>Exact official source</th></tr></thead>
                        <tbody>
                          {distributions.items.map((event) => {
                            const currentRevision = event.revisions.find((item) => item.is_current);
                            const source = currentRevision?.sources[0];
                            return (
                              <tr key={event.event_id}>
                                <td>{event.record_date}</td>
                                <td><strong>{currentRevision ? `₹${formatNav(currentRevision.amount_per_unit_inr)}` : "—"}</strong></td>
                                <td>{currentRevision ? `v${currentRevision.revision_number}` : "—"}<small>{currentRevision?.normalization_version ?? "Missing revision"}</small></td>
                                <td><code>{source?.source_content_signature.slice(0, 12) ?? "—"}</code><small>{source ? `${source.provider} · ${source.parser_version} · ${source.raw_source_value}` : "Missing provenance"}</small></td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <div className="batch-empty">
                      <strong>No canonical IDCW events for this option</strong>
                      <p>
                        No resolved official source rows have been published, or source rows may have
                        been retained outside the conservative cash-payout normalization gate.
                      </p>
                    </div>
                  )}
                  {distributions && distributions.total > distributions.items.length && (
                    <small>Showing the latest {distributions.items.length} of {distributions.total.toLocaleString()} events.</small>
                  )}
                </div>
              </>
            ) : null}
          </section>
        )}
      </div>

      <div className="batch-panel">
        <div className="batch-heading">
          <div>
            <span className="eyebrow">Latest 50 retrievals</span>
            <h3>Ingestion ledger</h3>
          </div>
          <span className="read-only-badge">Read-only</span>
        </div>
        {batches.length === 0 ? (
          <div className="batch-empty">
            <strong>No source captures yet</strong>
            <p>Run an audited ingestion command locally; this ledger will update on refresh.</p>
            <code>./scripts/sync_amfi_full.sh</code>
          </div>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr><th>Status</th><th>Source</th><th>Rows</th><th>Artifact</th><th>Retrieved</th></tr>
              </thead>
              <tbody>
                {batches.map((batch) => (
                  <tr key={batch.id}>
                    <td><span className={`batch-status ${batch.status}`}>{batch.status}</span></td>
                    <td><strong>{batch.source_type.replaceAll("_", " ")}</strong><small>{batch.parser_version}</small></td>
                    <td>{batch.rows_accepted.toLocaleString()}</td>
                    <td><code>{batch.artifact_sha256?.slice(0, 12) ?? "—"}</code><small>{formatBytes(batch.artifact_byte_size)}</small></td>
                    <td>{new Date(batch.started_at).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}
