import { describe, expect, it } from "vitest";

import type { ScreenerClassification } from "../api/client";
import {
  availableSchemeProductTypes,
  availableSchemeStructures,
  classificationsForProductType,
  classificationsForStructure,
} from "./classificationFilters";

const classifications: ScreenerClassification[] = [
  {
    classification_id: "open",
    classification: "Open Ended Schemes ( Equity Scheme )",
    structure_type: "open_ended",
    product_type: "mutual_fund",
    candidate_options: 22,
    eligible_options: 20,
    excluded_options: 2,
    mapping_version: "test:v1",
  },
  {
    classification_id: "interval",
    classification: "Interval Fund Schemes ( Income )",
    structure_type: "interval",
    product_type: "mutual_fund",
    candidate_options: 2,
    eligible_options: 2,
    excluded_options: 0,
    mapping_version: "test:v1",
  },
  {
    classification_id: "other",
    classification: "Future AMFI Family ( Example )",
    structure_type: "other",
    product_type: "etf",
    candidate_options: 1,
    eligible_options: 1,
    excluded_options: 0,
    mapping_version: "test:v1",
  },
];

describe("classification structure filters", () => {
  it("shows only structure types present in the response and preserves display order", () => {
    expect(availableSchemeStructures(classifications)).toEqual([
      "open_ended",
      "interval",
      "other",
    ]);
  });

  it("surfaces index funds and ETFs as separate product universes", () => {
    const withIndexFund: ScreenerClassification[] = [
      ...classifications,
      {
        ...classifications[0]!,
        classification_id: "index",
        classification: "Index Funds",
        product_type: "index_fund",
      },
    ];

    expect(availableSchemeProductTypes(withIndexFund)).toEqual([
      "mutual_fund",
      "index_fund",
      "etf",
    ]);
    expect(classificationsForProductType(withIndexFund, "etf")).toEqual([
      classifications[2],
    ]);
  });

  it("keeps only classifications from the selected structure", () => {
    expect(classificationsForStructure(classifications, "interval")).toEqual([
      classifications[1],
    ]);
  });
});
