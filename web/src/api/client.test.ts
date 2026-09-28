import { afterEach, describe, expect, it, vi } from "vitest";

import { screenFunds } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("IDCW screener API contract", () => {
  it("requests the IDCW universe and parses payout-yield evidence", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        classification_id: "equity-large-cap",
        classification: "Large Cap Fund",
        classification_mapping_version: "equity-large-cap:v1",
        items: [{
          rank: 1,
          amfi_scheme_code: "123456",
          scheme_name: "Example Direct IDCW Payout",
          fund_house_name: "Example Mutual Fund",
          scheme_classification: "Large Cap Fund",
          plan_type: "direct",
          option_type: "idcw",
          isin: "INF000000001",
          start_date: "2025-09-04",
          end_date: "2026-09-04",
          start_nav: "100",
          end_nav: "110",
          elapsed_days: 365,
          endpoint_staleness_days: 0,
          total_return_pct: "10",
          annualized_return_pct: "10",
          payout_amount_per_unit_inr: "6.6",
          payout_yield_pct: "6",
          payout_event_count: 4,
          latest_payout_record_date: "2026-08-15",
          payout_yield_rank: 1,
          payout_frequency_rank: 1,
          idcw_rank_score: "100",
        }],
        total: 1,
        limit: 25,
        offset: 0,
        as_of_date: "2026-09-04",
        target_start_date: "2025-09-04",
        horizon: "1y",
        ranking_metric: "payout_yield_frequency_score",
        ranking_method: "Equal-weight yield and frequency percentile score",
        return_basis: "nav_only",
        distribution_treatment: "record_date_payout_yield",
        day_count_convention: "actual/365",
        endpoint_tolerance_days: 7,
        candidate_options: 1,
        excluded_stale_endpoint: 0,
        excluded_insufficient_history: 0,
        excluded_no_payout_events: 0,
        excluded_items: [],
        exclusion_limit: 25,
        exclusion_offset: 0,
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await screenFunds({
      classificationId: "equity-large-cap",
      horizon: "1y",
      planType: "direct",
      optionType: "idcw",
      fundHouse: "",
      search: "",
      limit: 25,
      offset: 0,
      exclusionLimit: 25,
      exclusionOffset: 0,
    });

    expect(fetchMock).toHaveBeenCalledOnce();
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("option_type=idcw");
    expect(result.ranking_metric).toBe("payout_yield_frequency_score");
    expect(result.items[0]?.payout_event_count).toBe(4);
    expect(result.items[0]?.idcw_rank_score).toBe("100");
  });
});
