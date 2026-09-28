import type {
  SchemeProductType,
  SchemeStructure,
  ScreenerClassification,
} from "../api/client";

export const schemeProductTypeLabels: Record<SchemeProductType, string> = {
  mutual_fund: "Mutual funds",
  index_fund: "Index funds",
  etf: "ETFs",
  mixed: "Mixed aliases",
};

const schemeProductTypeOrder: SchemeProductType[] = [
  "mutual_fund",
  "index_fund",
  "etf",
  "mixed",
];

export const schemeStructureLabels: Record<SchemeStructure, string> = {
  open_ended: "Open ended",
  close_ended: "Close ended",
  interval: "Interval",
  other: "Other",
};

const schemeStructureOrder: SchemeStructure[] = [
  "open_ended",
  "close_ended",
  "interval",
  "other",
];

export function availableSchemeStructures(
  classifications: ScreenerClassification[],
): SchemeStructure[] {
  const present = new Set(classifications.map((item) => item.structure_type));
  return schemeStructureOrder.filter((structure) => present.has(structure));
}

export function classificationsForStructure(
  classifications: ScreenerClassification[],
  structure: SchemeStructure,
): ScreenerClassification[] {
  return classifications.filter((item) => item.structure_type === structure);
}

export function availableSchemeProductTypes(
  classifications: ScreenerClassification[],
): SchemeProductType[] {
  const present = new Set(classifications.map((item) => item.product_type));
  return schemeProductTypeOrder.filter((productType) => present.has(productType));
}

export function classificationsForProductType(
  classifications: ScreenerClassification[],
  productType: SchemeProductType,
): ScreenerClassification[] {
  return classifications.filter((item) => item.product_type === productType);
}
