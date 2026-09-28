import { describe, expect, it } from "vitest";

import type { HeatmapTile } from "../api/client";
import {
  heatmapScale,
  heatmapTileColor,
  heatmapTileTextColor,
  returnBasisLabel,
  rollingHeatmapPeriods,
  trailingHeatmapPeriods,
} from "./heatmap";

function tile(value: string | null): HeatmapTile {
  return {
    tile_id: "tile",
    label: "Tile",
    status: value === null ? "insufficient_history" : "available",
    reason_detail: null,
    value_pct: value,
    minimum_constituent_pct: value,
    maximum_constituent_pct: value,
    candidate_count: 1,
    constituent_count: value === null ? 0 : 1,
    excluded_count: value === null ? 1 : 0,
    excluded_stale_endpoint: 0,
    excluded_insufficient_history: value === null ? 1 : 0,
    sample_count: value === null ? 0 : 1,
    return_basis: "nav_only",
    structure_type: "open_ended",
    product_type: "mutual_fund",
    classification_mapping_version: "alias:v1",
    period_start_date_min: null,
    period_start_date_max: null,
    period_end_date_min: null,
    period_end_date_max: null,
    maximum_endpoint_staleness_days: null,
  };
}

describe("heatmap presentation model", () => {
  it("offers every requested trailing and rolling period", () => {
    expect(trailingHeatmapPeriods.map((period) => period.value)).toEqual([
      "1m", "3m", "6m", "1y", "3y", "5y", "10y",
    ]);
    expect(rollingHeatmapPeriods.map((period) => period.value)).toEqual([
      "rolling_1y", "rolling_3y", "rolling_5y", "rolling_10y",
    ]);
  });

  it("uses a symmetric data scale with distinct gain, loss, and unavailable colors", () => {
    const scale = heatmapScale([tile("12"), tile("-8"), tile(null)]);
    expect(scale).toBe(12);
    expect(heatmapTileColor("12", scale)).toContain("hsl(153");
    expect(heatmapTileColor("-8", scale)).toContain("hsl(7");
    expect(heatmapTileColor(null, scale)).toBe("hsl(45 9% 86%)");
    expect(heatmapTileTextColor("12", scale)).toBe("#ffffff");
  });

  it("does not conflate NAV, price, TRI, and NTR return bases", () => {
    expect(returnBasisLabel("nav_only")).toContain("Growth NAV");
    expect(returnBasisLabel("price")).toContain("distributions excluded");
    expect(returnBasisLabel("gross_total_return")).toContain("TRI");
    expect(returnBasisLabel("net_total_return")).toContain("NTR");
  });
});
