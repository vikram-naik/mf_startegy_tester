import type { SchemePerformance } from "../api/client";

export const SUMMARY_CAGR_WINDOWS = [3, 5, 10] as const;

type RollingReturnSummary = SchemePerformance["rolling_returns"][number];

export function latestRollingReturn(
  rollingReturns: SchemePerformance["rolling_returns"],
  windowYears: number,
): RollingReturnSummary["latest"] {
  return rollingReturns.find((item) => item.window_years === windowYears)?.latest ?? null;
}
