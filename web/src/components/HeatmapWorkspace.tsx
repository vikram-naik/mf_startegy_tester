import { useEffect, useMemo, useState } from "react";

import {
  getHeatmap,
  listClassifications,
  screenFunds,
  type FundScreener,
  type Heatmap,
  type HeatmapPeriod,
  type HeatmapUniverse,
  type ScreenerClassification,
} from "../api/client";
import {
  heatmapScale,
  heatmapTileColor,
  heatmapTileTextColor,
  returnBasisLabel,
  rollingHeatmapPeriods,
  trailingHeatmapPeriods,
} from "../model/heatmap";

type HeatmapView = HeatmapUniverse | "idcw";

const universeLabels: Record<HeatmapView, string> = {
  funds: "Funds",
  idcw: "IDCW payouts",
  benchmarks: "Benchmarks",
  indices: "Indices",
};

function message(error: unknown): string {
  return error instanceof Error ? error.message : "Unable to load heatmap data.";
}

function percent(value: string | null): string {
  return value === null ? "—" : `${Number(value).toFixed(2)}%`;
}

function decimal(value: string | null, maximumFractionDigits = 4): string {
  return value === null
    ? "—"
    : Number(value).toLocaleString(undefined, { maximumFractionDigits });
}

function endpointRange(minimum: string | null, maximum: string | null): string {
  if (minimum === null || maximum === null) return "Unavailable";
  return minimum === maximum ? minimum : `${minimum} – ${maximum}`;
}

function metricLabel(heatmap: Heatmap): string {
  if (heatmap.metric === "series_return_pct") {
    return heatmap.period === "1m" || heatmap.period === "3m" || heatmap.period === "6m"
      ? "Absolute return"
      : "Actual/365 CAGR";
  }
  if (heatmap.metric === "median_constituent_return_pct") {
    return heatmap.period === "1m" || heatmap.period === "3m" || heatmap.period === "6m"
      ? "Median absolute return"
      : "Median actual/365 CAGR";
  }
  return "Median rolling actual/365 return";
}

function idcwScale(results: FundScreener | null): number {
  return Math.max(
    1,
    ...(results?.items.flatMap((item) =>
      item.payout_yield_pct === null ? [] : [Math.abs(Number(item.payout_yield_pct))],
    ) ?? []),
  );
}

export function HeatmapWorkspace() {
  const [universe, setUniverse] = useState<HeatmapView>("funds");
  const [period, setPeriod] = useState<HeatmapPeriod>("1m");
  const [planType, setPlanType] = useState<"direct" | "regular">("direct");
  const [heatmap, setHeatmap] = useState<Heatmap | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [idcwClassifications, setIdcwClassifications] = useState<ScreenerClassification[]>([]);
  const [idcwClassification, setIdcwClassification] = useState("");
  const [idcwResults, setIdcwResults] = useState<FundScreener | null>(null);

  useEffect(() => {
    if (universe === "idcw") {
      setHeatmap(null);
      setIsLoading(false);
      return;
    }
    let current = true;
    setIsLoading(true);
    setError(null);
    getHeatmap({ universe, period, planType })
      .then((loaded) => {
        if (current) setHeatmap(loaded);
      })
      .catch((reason: unknown) => {
        if (current) {
          setHeatmap(null);
          setError(message(reason));
        }
      })
      .finally(() => {
        if (current) setIsLoading(false);
      });
    return () => {
      current = false;
    };
  }, [universe, period, planType]);

  useEffect(() => {
    if (universe !== "idcw") return;
    let current = true;
    setIsLoading(true);
    setError(null);
    listClassifications(planType, "idcw", "1y", "")
      .then((items) => {
        if (!current) return;
        setIdcwClassifications(items);
        setIdcwClassification((value) =>
          items.some((item) => item.classification_id === value)
            ? value
            : (items[0]?.classification_id ?? ""),
        );
      })
      .catch((reason: unknown) => current && setError(message(reason)))
      .finally(() => current && setIsLoading(false));
    return () => { current = false; };
  }, [universe, planType]);

  useEffect(() => {
    if (universe !== "idcw" || !idcwClassification) {
      setIdcwResults(null);
      return;
    }
    let current = true;
    setIsLoading(true);
    setError(null);
    screenFunds({
      classificationId: idcwClassification,
      horizon: "1y",
      planType,
      optionType: "idcw",
      fundHouse: "",
      search: "",
      limit: 100,
      offset: 0,
      exclusionLimit: 1,
      exclusionOffset: 0,
    })
      .then((loaded) => current && setIdcwResults(loaded))
      .catch((reason: unknown) => current && setError(message(reason)))
      .finally(() => current && setIsLoading(false));
    return () => { current = false; };
  }, [universe, idcwClassification, planType]);

  const colorScale = useMemo(() => heatmapScale(heatmap?.tiles ?? []), [heatmap]);
  const availableCount = heatmap?.tiles.filter((tile) => tile.status === "available").length ?? 0;
  const totalExcluded = heatmap?.tiles.reduce((total, tile) => total + tile.excluded_count, 0) ?? 0;
  const payoutScale = useMemo(() => idcwScale(idcwResults), [idcwResults]);

  return (
    <section className="heatmap-workspace">
      <div className="page-heading">
        <div>
          <span className="eyebrow">Cross-market momentum view</span>
          <h1>Heatmaps</h1>
        </div>
        <p>
          Compare classification-level fund returns, ranked IDCW payout yields, official Nifty
          total-return benchmarks, and price indices.
        </p>
      </div>

      <div className="heatmap-card">
        <div className="heatmap-toolbar">
          <fieldset className="heatmap-universe-filter">
            <legend>Heatmap universe</legend>
            <div className="segmented-control heatmap-universe-options">
              {(Object.keys(universeLabels) as HeatmapView[]).map((candidate) => (
                <button
                  key={candidate}
                  type="button"
                  className={candidate === universe ? "active" : ""}
                  aria-pressed={candidate === universe}
                  onClick={() => {
                    setUniverse(candidate);
                    if (candidate === "idcw") setPeriod("1y");
                  }}
                >
                  {universeLabels[candidate]}
                </button>
              ))}
            </div>
          </fieldset>
          {universe === "funds" || universe === "idcw" ? (
            <fieldset className="heatmap-plan-filter">
              <legend>Plan</legend>
              <div className="segmented-control">
                {(["direct", "regular"] as const).map((plan) => (
                  <button
                    key={plan}
                    type="button"
                    className={plan === planType ? "active" : ""}
                    aria-pressed={plan === planType}
                    onClick={() => setPlanType(plan)}
                  >
                    {plan === "direct" ? "Direct" : "Regular"}
                  </button>
                ))}
              </div>
            </fieldset>
          ) : null}
          {universe === "idcw" ? (
            <label className="heatmap-classification-filter">
              AMFI classification
              <select
                value={idcwClassification}
                onChange={(event) => setIdcwClassification(event.target.value)}
              >
                {idcwClassifications.length === 0 ? (
                  <option value="">No eligible IDCW classifications</option>
                ) : idcwClassifications.map((item) => (
                  <option key={item.classification_id} value={item.classification_id}>
                    {item.classification} · {item.eligible_options}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
        </div>

        {universe !== "idcw" ? <div className="heatmap-period-panel">
          <fieldset>
            <legend>Trailing period · requested through today</legend>
            <div className="heatmap-period-options">
              {trailingHeatmapPeriods.map((candidate) => (
                <label key={candidate.value}>
                  <input
                    type="radio"
                    name="heatmap-period"
                    value={candidate.value}
                    checked={period === candidate.value}
                    onChange={() => setPeriod(candidate.value)}
                  />
                  <span>{candidate.label}</span>
                </label>
              ))}
            </div>
          </fieldset>
          <fieldset>
            <legend>
              Rolling period · {universe === "funds" ? "monthly" : "daily"} observations
            </legend>
            <div className="heatmap-period-options rolling">
              {rollingHeatmapPeriods.map((candidate) => (
                <label key={candidate.value}>
                  <input
                    type="radio"
                    name="heatmap-period"
                    value={candidate.value}
                    checked={period === candidate.value}
                    onChange={() => setPeriod(candidate.value)}
                  />
                  <span>{candidate.label}</span>
                </label>
              ))}
            </div>
          </fieldset>
        </div> : (
          <div className="heatmap-period-panel idcw-period-note">
            <p>Fixed trailing period: 12 months · tile color: payout yield · tile order: equal-weight yield and payout-frequency percentile rank.</p>
          </div>
        )}

        {error ? <div className="browser-message heatmap-error">{error}</div> : null}
        {universe === "idcw" && idcwResults ? (
          <>
            <div className="heatmap-summary">
              <div><span>As of</span><strong>{idcwResults.as_of_date}</strong></div>
              <div><span>Metric</span><strong>Payout yield + frequency</strong></div>
              <div><span>Ranked funds shown</span><strong>{idcwResults.items.length} / {idcwResults.total}</strong></div>
              <div><span>Excluded</span><strong>{(idcwResults.excluded_stale_endpoint + idcwResults.excluded_insufficient_history + idcwResults.excluded_no_payout_events).toLocaleString()}</strong></div>
            </div>
            <div className={`heatmap-grid ${isLoading ? "is-loading" : ""}`} aria-busy={isLoading}>
              {idcwResults.items.length === 0 ? (
                <div className="heatmap-empty">
                  <strong>No eligible IDCW payout tiles</strong>
                  <p>No option has both acceptable NAV endpoints and canonical payouts in this window.</p>
                </div>
              ) : idcwResults.items.map((fund) => {
                const color = heatmapTileColor(fund.payout_yield_pct, payoutScale);
                const textColor = heatmapTileTextColor(fund.payout_yield_pct, payoutScale);
                return (
                  <article
                    key={fund.amfi_scheme_code}
                    className="heatmap-tile idcw-heatmap-tile"
                    style={{ backgroundColor: color, color: textColor }}
                  >
                    <div className="heatmap-tile-heading">
                      <strong>#{fund.rank} · {fund.scheme_name}</strong>
                      <span>{percent(fund.payout_yield_pct)}</span>
                    </div>
                    <p>{fund.fund_house_name} · AMFI {fund.amfi_scheme_code}</p>
                    <dl>
                      <div><dt>Combined score</dt><dd>{decimal(fund.idcw_rank_score, 1)} / 100</dd></div>
                      <div><dt>Yield rank</dt><dd>{fund.payout_yield_rank === null ? "—" : `#${fund.payout_yield_rank}`}</dd></div>
                      <div><dt>Payout frequency</dt><dd>{fund.payout_event_count} declarations · {fund.payout_frequency_rank === null ? "unranked" : `rank #${fund.payout_frequency_rank}`}</dd></div>
                      <div><dt>Payout amount</dt><dd>₹{decimal(fund.payout_amount_per_unit_inr)} / unit</dd></div>
                      <div><dt>Latest record date</dt><dd>{fund.latest_payout_record_date ?? "—"}</dd></div>
                    </dl>
                  </article>
                );
              })}
            </div>
          </>
        ) : heatmap ? (
          <>
            <div className="heatmap-summary">
              <div><span>As of</span><strong>{heatmap.requested_as_of_date}</strong></div>
              <div><span>Metric</span><strong>{metricLabel(heatmap)}</strong></div>
              <div><span>Available tiles</span><strong>{availableCount} / {heatmap.tiles.length}</strong></div>
              <div><span>Excluded constituents</span><strong>{totalExcluded.toLocaleString()}</strong></div>
            </div>

            <div className={`heatmap-grid ${isLoading ? "is-loading" : ""}`} aria-busy={isLoading}>
              {heatmap.tiles.length === 0 ? (
                <div className="heatmap-empty">
                  <strong>No eligible heatmap tiles</strong>
                  <p>The local dataset has no series satisfying this universe and period.</p>
                </div>
              ) : heatmap.tiles.map((tile) => {
                const color = heatmapTileColor(tile.value_pct, colorScale);
                const textColor = heatmapTileTextColor(tile.value_pct, colorScale);
                return (
                  <article
                    key={tile.tile_id}
                    className={`heatmap-tile ${tile.status !== "available" ? "unavailable" : ""}`}
                    style={{ backgroundColor: color, color: textColor }}
                  >
                    <div className="heatmap-tile-heading">
                      <strong>{tile.label}</strong>
                      <span>{percent(tile.value_pct)}</span>
                    </div>
                    <p>{returnBasisLabel(tile.return_basis)}</p>
                    {tile.status === "available" ? (
                      <dl>
                        <div>
                          <dt>{heatmap.mode === "rolling" ? "Constituent/sample range" : "Constituent range"}</dt>
                          <dd>{percent(tile.minimum_constituent_pct)} to {percent(tile.maximum_constituent_pct)}</dd>
                        </div>
                        <div>
                          <dt>Coverage</dt>
                          <dd>
                            {tile.constituent_count}/{tile.candidate_count} constituents
                            {heatmap.mode === "rolling" ? ` · ${tile.sample_count.toLocaleString()} periods` : ""}
                            {tile.excluded_count > 0 ? ` · ${tile.excluded_stale_endpoint} stale / ${tile.excluded_insufficient_history} history` : ""}
                          </dd>
                        </div>
                        <div>
                          <dt>Actual starts</dt>
                          <dd>{endpointRange(tile.period_start_date_min, tile.period_start_date_max)}</dd>
                        </div>
                        <div>
                          <dt>Actual ends</dt>
                          <dd>{endpointRange(tile.period_end_date_min, tile.period_end_date_max)}</dd>
                        </div>
                      </dl>
                    ) : (
                      <p className="heatmap-unavailable-reason">{tile.reason_detail}</p>
                    )}
                  </article>
                );
              })}
            </div>
          </>
        ) : isLoading ? (
          <div className="heatmap-loading">Calculating heatmap from current valid observations…</div>
        ) : null}
      </div>

      {universe === "idcw" && idcwResults ? (
        <div className="heatmap-methodology">
          <p><strong>Ranking.</strong> {idcwResults.ranking_method}.</p>
          <p><strong>Yield.</strong> Current canonical INR-per-unit declarations with record dates after {idcwResults.target_start_date} and through {idcwResults.as_of_date}, divided by endpoint NAV.</p>
          <p><strong>Frequency.</strong> Count of those canonical declarations; record date is not proof of payment date. Missing payout evidence is excluded, never zero-filled.</p>
          <p><strong>Limitation.</strong> Payout yield is not total return or a forecast. Current metadata makes this non-survivorship-free. At most 100 ranked options are rendered per classification.</p>
        </div>
      ) : heatmap ? (
        <div className="heatmap-methodology">
          <p><strong>Aggregation.</strong> {heatmap.aggregation_method}.</p>
          <p><strong>Endpoints.</strong> Target dates allow {heatmap.endpoint_tolerance_days} calendar days; missing observations are never zero or forward-filled. {heatmap.observation_frequency}.</p>
          <p><strong>Return basis.</strong> {heatmap.distribution_treatment}. Day count is {heatmap.day_count_convention}{heatmap.rolling_start_rule ? `; ${heatmap.rolling_start_rule}` : ""}.</p>
          <p><strong>Limitation.</strong> {heatmap.current_universe_limitation}</p>
        </div>
      ) : null}
    </section>
  );
}
