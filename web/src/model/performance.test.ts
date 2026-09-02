import { describe, expect, it } from "vitest";

import { latestRollingReturn, SUMMARY_CAGR_WINDOWS } from "./performance";

const threeYearReturn = {
  start_date: "2023-08-14",
  end_date: "2026-08-14",
  start_nav: "100",
  end_nav: "150",
  elapsed_days: 1096,
  total_return_pct: "50",
  annualized_return_pct: "14.465",
};

describe("performance summary", () => {
  it("uses the latest point-to-point return for the requested CAGR window", () => {
    const result = latestRollingReturn(
      [
        {
          window_years: 3,
          sample_count: 42,
          latest: threeYearReturn,
          minimum_annualized_return_pct: "-1",
          median_annualized_return_pct: "10",
          mean_annualized_return_pct: "9",
          maximum_annualized_return_pct: "18",
          positive_periods_pct: "95",
        },
      ],
      3,
    );

    expect(SUMMARY_CAGR_WINDOWS).toEqual([3, 5, 10]);
    expect(result).toEqual(threeYearReturn);
  });

  it("does not substitute another period when the requested history is unavailable", () => {
    expect(latestRollingReturn([], 10)).toBeNull();
  });
});
