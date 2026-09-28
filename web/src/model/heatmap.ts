import type { HeatmapPeriod, HeatmapTile } from "../api/client";

export const trailingHeatmapPeriods: ReadonlyArray<{ value: HeatmapPeriod; label: string }> = [
  { value: "1m", label: "1M" },
  { value: "3m", label: "3M" },
  { value: "6m", label: "6M" },
  { value: "1y", label: "1Y" },
  { value: "3y", label: "3Y" },
  { value: "5y", label: "5Y" },
  { value: "10y", label: "10Y" },
];

export const rollingHeatmapPeriods: ReadonlyArray<{ value: HeatmapPeriod; label: string }> = [
  { value: "rolling_1y", label: "1Y" },
  { value: "rolling_3y", label: "3Y" },
  { value: "rolling_5y", label: "5Y" },
  { value: "rolling_10y", label: "10Y" },
];

export function heatmapScale(tiles: HeatmapTile[]): number {
  const magnitudes = tiles
    .map((tile) => Math.abs(Number(tile.value_pct)))
    .filter((value) => Number.isFinite(value));
  return Math.max(1, ...magnitudes);
}

export function heatmapTileColor(value: string | null, scale: number): string {
  if (value === null) return "hsl(45 9% 86%)";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "hsl(45 9% 86%)";
  const intensity = Math.min(1, Math.abs(numeric) / Math.max(scale, 1));
  const lightness = 94 - intensity * 43;
  return numeric >= 0
    ? `hsl(153 43% ${lightness.toFixed(1)}%)`
    : `hsl(7 55% ${lightness.toFixed(1)}%)`;
}

export function heatmapTileTextColor(value: string | null, scale: number): string {
  if (value === null) return "#4f5753";
  const intensity = Math.abs(Number(value)) / Math.max(scale, 1);
  return intensity > 0.62 ? "#ffffff" : "#17241f";
}

export function returnBasisLabel(value: HeatmapTile["return_basis"]): string {
  if (value === "gross_total_return") return "TRI · gross total return";
  if (value === "net_total_return") return "NTR · net total return";
  if (value === "price") return "Price index · distributions excluded";
  return "Growth NAV · IDCW excluded";
}
