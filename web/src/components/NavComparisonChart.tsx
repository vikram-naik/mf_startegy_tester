import { useMemo, useState } from "react";

import type { FundComparison } from "../api/client";

const COLORS = ["#176b52", "#b36b28", "#365f9d", "#8a4f7d", "#63723a"];
const WIDTH = 1000;
const HEIGHT = 360;
const MARGIN = { top: 22, right: 28, bottom: 42, left: 62 };

type ChartMode = "normalized" | "raw";

function dateNumber(value: string): number {
  return new Date(`${value}T00:00:00Z`).getTime();
}

export function NavComparisonChart({ comparison }: { comparison: FundComparison }) {
  const [mode, setMode] = useState<ChartMode>("normalized");
  const chart = useMemo(() => {
    const allPoints = comparison.series.flatMap((series) => series.points);
    const dates = allPoints.map((point) => dateNumber(point.nav_date));
    const values = allPoints.map((point) =>
      Number(mode === "normalized" ? point.normalized_value : point.nav_value),
    );
    const minDate = Math.min(...dates);
    const maxDate = Math.max(...dates);
    const rawMin = Math.min(...values);
    const rawMax = Math.max(...values);
    const padding = Math.max((rawMax - rawMin) * 0.08, rawMax * 0.01, 0.1);
    const minValue = rawMin - padding;
    const maxValue = rawMax + padding;
    const plotWidth = WIDTH - MARGIN.left - MARGIN.right;
    const plotHeight = HEIGHT - MARGIN.top - MARGIN.bottom;
    const x = (date: string) =>
      MARGIN.left + ((dateNumber(date) - minDate) / Math.max(maxDate - minDate, 1)) * plotWidth;
    const y = (value: number) =>
      MARGIN.top + (1 - (value - minValue) / Math.max(maxValue - minValue, 0.0001)) * plotHeight;
    return { minDate, maxDate, minValue, maxValue, plotHeight, plotWidth, x, y };
  }, [comparison, mode]);

  const yTicks = Array.from({ length: 5 }, (_, index) => {
    const fraction = index / 4;
    const value = chart.maxValue - (chart.maxValue - chart.minValue) * fraction;
    return { value, y: MARGIN.top + chart.plotHeight * fraction };
  });

  return (
    <div className="comparison-chart-shell">
      <div className="chart-toolbar">
        <div>
          <strong>NAV performance comparison</strong>
          <span>{comparison.start_date} to {comparison.end_date}</span>
        </div>
        <div className="segmented-control" aria-label="Chart scale">
          <button className={mode === "normalized" ? "active" : ""} onClick={() => setMode("normalized")}>Normalized to 100</button>
          <button className={mode === "raw" ? "active" : ""} onClick={() => setMode("raw")}>Raw NAV</button>
        </div>
      </div>
      <svg className="nav-chart" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label="NAV comparison chart">
        {yTicks.map((tick) => (
          <g key={tick.y}>
            <line x1={MARGIN.left} x2={WIDTH - MARGIN.right} y1={tick.y} y2={tick.y} className="chart-gridline" />
            <text x={MARGIN.left - 10} y={tick.y + 4} textAnchor="end" className="chart-axis-label">
              {tick.value.toFixed(mode === "normalized" ? 0 : 2)}
            </text>
          </g>
        ))}
        <text x={MARGIN.left} y={HEIGHT - 14} className="chart-axis-label">{comparison.start_date}</text>
        <text x={WIDTH - MARGIN.right} y={HEIGHT - 14} textAnchor="end" className="chart-axis-label">{comparison.end_date}</text>
        {comparison.series.map((series, seriesIndex) => {
          const color = COLORS[seriesIndex % COLORS.length];
          const polyline = series.points.map((point) => {
            const value = Number(mode === "normalized" ? point.normalized_value : point.nav_value);
            return `${chart.x(point.nav_date)},${chart.y(value)}`;
          }).join(" ");
          return (
            <g key={series.amfi_scheme_code}>
              <polyline points={polyline} fill="none" stroke={color} strokeWidth="2.2" vectorEffect="non-scaling-stroke" />
              {series.distributions.map((event) => {
                const eventTime = dateNumber(event.record_date);
                const point = series.points.reduce((nearest, candidate) =>
                  Math.abs(dateNumber(candidate.nav_date) - eventTime)
                    < Math.abs(dateNumber(nearest.nav_date) - eventTime) ? candidate : nearest,
                );
                const value = Number(mode === "normalized" ? point.normalized_value : point.nav_value);
                return (
                  <circle key={`${series.amfi_scheme_code}-${event.record_date}`} cx={chart.x(point.nav_date)} cy={chart.y(value)} r="5" fill="#fff" stroke={color} strokeWidth="2">
                    <title>{`${series.scheme_name}: IDCW ₹${event.amount_per_unit_inr} per unit, record date ${event.record_date}`}</title>
                  </circle>
                );
              })}
            </g>
          );
        })}
      </svg>
      <div className="chart-legend">
        {comparison.series.map((series, index) => (
          <div key={series.amfi_scheme_code}>
            <span className="legend-swatch" style={{ background: COLORS[index % COLORS.length] }} />
            <span><strong>{series.scheme_name}</strong><small>{series.fund_house_name} · {series.amfi_scheme_code}</small></span>
          </div>
        ))}
      </div>
      <p className="chart-footnote">
        Circles annotate canonical IDCW record-date events at the nearest valid NAV observation;
        the tooltip retains the exact record date and INR amount per unit. Payout cash flows are not
        added to NAV returns, and payment dates are not inferred.
      </p>
    </div>
  );
}
