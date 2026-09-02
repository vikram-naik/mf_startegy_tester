import { describe, expect, it } from "vitest";

import { distributionCoverageMessage } from "./distributionCoverage";

const baseCoverage = {
  assessment_run_id: "run",
  assessment_version: "test",
  source_row_count: 2,
  canonical_source_row_count: 2,
  blocked_source_row_count: 0,
  canonical_event_count: 2,
  first_source_record_date: "2020-01-01",
  last_source_record_date: "2021-01-01",
  assessed_at: "2026-08-17T00:00:00Z",
} as const;

describe("distribution coverage message", () => {
  it("does not describe present events as complete history", () => {
    const message = distributionCoverageMessage({
      ...baseCoverage,
      coverage_status: "events_present",
    });

    expect(message.tone).toBe("available");
    expect(message.detail).toContain("does not certify complete history");
  });

  it("distinguishes blocked evidence from missing official-source evidence", () => {
    const blocked = distributionCoverageMessage({
      ...baseCoverage,
      coverage_status: "blocked_source_rows",
      canonical_source_row_count: 0,
      blocked_source_row_count: 2,
      canonical_event_count: 0,
    });
    const empty = distributionCoverageMessage({
      ...baseCoverage,
      coverage_status: "unverified_empty",
      source_row_count: 0,
      canonical_source_row_count: 0,
      canonical_event_count: 0,
      first_source_record_date: null,
      last_source_record_date: null,
    });

    expect(blocked.label).toBe("Source rows blocked");
    expect(empty.label).toBe("Official-source coverage unverified");
    expect(empty.detail).toContain("not confirmation");
  });
});
