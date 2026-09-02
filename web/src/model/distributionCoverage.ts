import type { DistributionEventBrowser } from "../api/client";

type DistributionCoverage = DistributionEventBrowser["coverage"];

export type DistributionCoverageMessage = {
  label: string;
  detail: string;
  tone: "available" | "blocked" | "missing";
};

export function distributionCoverageMessage(
  coverage: DistributionCoverage,
): DistributionCoverageMessage {
  if (coverage === null) {
    return {
      label: "Coverage not assessed",
      detail: "Run the distribution coverage assessment after normalization.",
      tone: "missing",
    };
  }
  if (coverage.coverage_status === "events_present") {
    const blockedDetail = coverage.blocked_source_row_count > 0
      ? ` ${coverage.blocked_source_row_count.toLocaleString()} additional source rows remain blocked.`
      : "";
    return {
      label: "Canonical events present",
      detail:
        `${coverage.canonical_event_count.toLocaleString()} events from ` +
        `${coverage.source_row_count.toLocaleString()} resolved official source rows. ` +
        `Presence does not certify complete history.${blockedDetail}`,
      tone: "available",
    };
  }
  if (coverage.coverage_status === "blocked_source_rows") {
    return {
      label: "Source rows blocked",
      detail:
        `${coverage.source_row_count.toLocaleString()} resolved official rows were retained, ` +
        "but none can yet be published as canonical cash payouts.",
      tone: "blocked",
    };
  }
  return {
    label: "Official-source coverage unverified",
    detail:
      "No resolved AMFI or AMC distribution rows were captured. This is a source gap, not " +
      "confirmation that the option made no payouts.",
    tone: "missing",
  };
}
