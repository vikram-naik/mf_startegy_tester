import { z } from "zod";

const apiBaseUrl =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "") ?? "http://127.0.0.1:8000/api/v1";

const ingestionBatchSchema = z.object({
  id: z.string(),
  provider: z.string(),
  source_type: z.string(),
  status: z.enum(["running", "completed", "failed"]),
  parser_version: z.string(),
  started_at: z.string(),
  completed_at: z.string().nullable(),
  artifact_sha256: z.string().nullable(),
  artifact_byte_size: z.number().int().nonnegative().nullable(),
  artifact_reused: z.boolean(),
  rows_received: z.number().int().nonnegative(),
  rows_accepted: z.number().int().nonnegative(),
  rows_rejected: z.number().int().nonnegative(),
  error_details: z.string().nullable(),
});

export type IngestionBatch = z.infer<typeof ingestionBatchSchema>;

const navSyncRunSchema = z.object({
  id: z.string(),
  mode: z.enum(["full", "incremental"]),
  status: z.enum(["running", "completed", "failed"]),
  requested_start_date: z.string(),
  requested_end_date: z.string(),
  funds_total: z.number().int().nonnegative(),
  funds_completed: z.number().int().nonnegative(),
  chunks_completed: z.number().int().nonnegative(),
  rows_received: z.number().int().nonnegative(),
  rows_inserted: z.number().int().nonnegative(),
  rows_unchanged: z.number().int().nonnegative(),
  rows_revised: z.number().int().nonnegative(),
  rows_quarantined: z.number().int().nonnegative(),
  latest_nav_date_found: z.string().nullable(),
  started_at: z.string(),
  completed_at: z.string().nullable(),
  error_details: z.string().nullable(),
});

const dataCoverageSchema = z.object({
  active_funds: z.number().int().nonnegative(),
  fully_covered_funds: z.number().int().nonnegative(),
  scheme_options: z.number().int().nonnegative(),
  valid_nav_rows: z.number().int().nonnegative(),
  quarantined_nav_rows: z.number().int().nonnegative(),
  earliest_nav_date: z.string().nullable(),
  latest_nav_date: z.string().nullable(),
  latest_sync_run: navSyncRunSchema.nullable(),
});

export type DataCoverage = z.infer<typeof dataCoverageSchema>;

const fundHouseSchema = z.object({
  mutual_fund_id: z.string(),
  name: z.string(),
  is_active: z.boolean(),
  scheme_options: z.number().int().nonnegative(),
  completed_through: z.string().nullable(),
  latest_nav_date_found: z.string().nullable(),
  fully_covered: z.boolean(),
});

const schemeCategorySchema = z.object({
  classification_id: z.string(),
  classification: z.string(),
  structure_type: z.enum(["open_ended", "close_ended", "interval", "other"]),
  scheme_options: z.number().int().nonnegative(),
});

const screenerClassificationSchema = z.object({
  classification_id: z.string(),
  classification: z.string(),
  structure_type: z.enum(["open_ended", "close_ended", "interval", "other"]),
  product_type: z.enum(["mutual_fund", "index_fund", "etf", "mixed"]),
  candidate_options: z.number().int().positive(),
  eligible_options: z.number().int().positive(),
  excluded_options: z.number().int().nonnegative(),
  mapping_version: z.string(),
});

const schemeStructureSchema = z.enum(["open_ended", "close_ended", "interval", "other"]);

const classificationAliasSchema = z.object({
  id: z.string(),
  name: z.string(),
  structure_type: schemeStructureSchema,
  status: z.enum(["active", "inactive"]),
  version: z.number().int().positive(),
  classification_ids: z.array(z.string()),
  updated_at: z.string(),
});

const classificationAliasSourceSchema = z.object({
  classification_id: z.string(),
  amfi_classification: z.string(),
  structure_type: schemeStructureSchema,
  raw_labels: z.array(z.string()),
  alias_id: z.string().nullable(),
});

const classificationAliasRevisionSchema = z.object({
  alias_id: z.string(),
  version: z.number().int().positive(),
  name: z.string(),
  structure_type: schemeStructureSchema,
  status: z.enum(["active", "inactive"]),
  classification_ids: z.array(z.string()),
  reason: z.string(),
  created_at: z.string(),
});

const classificationAliasManagementSchema = z.object({
  aliases: z.array(classificationAliasSchema),
  source_classifications: z.array(classificationAliasSourceSchema),
  revisions: z.array(classificationAliasRevisionSchema),
});

const schemeBrowserItemSchema = z.object({
  amfi_scheme_code: z.string(),
  scheme_name: z.string(),
  fund_house_name: z.string(),
  scheme_classification: z.string(),
  plan_type: z.enum(["direct", "regular", "unknown"]),
  option_type: z.enum(["growth", "idcw", "bonus", "unknown"]),
  isin_payout_or_growth: z.string().nullable(),
  isin_reinvestment: z.string().nullable(),
  first_nav_date: z.string(),
  latest_nav_date: z.string(),
  latest_nav_value: z.string(),
  quality_status: z.enum(["valid", "error"]),
});

const schemeBrowserSchema = z.object({
  items: z.array(schemeBrowserItemSchema),
  total: z.number().int().nonnegative(),
  limit: z.number().int().positive(),
  offset: z.number().int().nonnegative(),
});

const screenerFundSchema = z.object({
  rank: z.number().int().positive(),
  amfi_scheme_code: z.string(),
  scheme_name: z.string(),
  fund_house_name: z.string(),
  scheme_classification: z.string(),
  plan_type: z.enum(["direct", "regular"]),
  option_type: z.enum(["growth", "idcw"]),
  isin: z.string().nullable(),
  start_date: z.string(),
  end_date: z.string(),
  start_nav: z.string(),
  end_nav: z.string(),
  elapsed_days: z.number().int().positive(),
  endpoint_staleness_days: z.number().int().nonnegative(),
  total_return_pct: z.string(),
  annualized_return_pct: z.string().nullable(),
  payout_amount_per_unit_inr: z.string().nullable(),
  payout_yield_pct: z.string().nullable(),
  payout_event_count: z.number().int().nonnegative(),
  latest_payout_record_date: z.string().nullable(),
  payout_yield_rank: z.number().int().positive().nullable(),
  payout_frequency_rank: z.number().int().positive().nullable(),
  idcw_rank_score: z.string().nullable(),
});

const fundScreenerSchema = z.object({
  classification_id: z.string(),
  classification: z.string(),
  classification_mapping_version: z.string(),
  items: z.array(screenerFundSchema),
  total: z.number().int().nonnegative(),
  limit: z.number().int().positive(),
  offset: z.number().int().nonnegative(),
  as_of_date: z.string(),
  target_start_date: z.string(),
  horizon: z.enum(["1m", "3m", "6m", "1y", "3y", "5y", "10y"]),
  ranking_metric: z.enum([
    "total_return_pct", "annualized_return_pct", "payout_yield_frequency_score",
  ]),
  ranking_method: z.string().nullable(),
  return_basis: z.literal("nav_only"),
  distribution_treatment: z.enum(["excluded", "record_date_payout_yield"]),
  day_count_convention: z.literal("actual/365"),
  endpoint_tolerance_days: z.number().int().nonnegative(),
  candidate_options: z.number().int().nonnegative(),
  excluded_stale_endpoint: z.number().int().nonnegative(),
  excluded_insufficient_history: z.number().int().nonnegative(),
  excluded_no_payout_events: z.number().int().nonnegative(),
  excluded_items: z.array(z.object({
    amfi_scheme_code: z.string(),
    scheme_name: z.string(),
    fund_house_name: z.string(),
    scheme_classification: z.string(),
    plan_type: z.enum(["direct", "regular"]),
    option_type: z.enum(["growth", "idcw"]),
    isin: z.string().nullable(),
    reason: z.enum(["stale_endpoint", "insufficient_history", "no_payout_events"]),
    reason_detail: z.string(),
    first_nav_date: z.string(),
    latest_nav_date: z.string().nullable(),
    latest_nav: z.string().nullable(),
    endpoint_staleness_days: z.number().int().nonnegative().nullable(),
  })),
  exclusion_limit: z.number().int().positive(),
  exclusion_offset: z.number().int().nonnegative(),
});

const benchmarkSeriesSchema = z.object({
  instrument_id: z.string(),
  display_name: z.string(),
  benchmark_family: z.string(),
  provider: z.literal("nifty_indices"),
  instrument_type: z.enum([
    "price_index",
    "gross_total_return_index",
    "net_total_return_index",
  ]),
  return_basis: z.enum(["price", "gross_total_return", "net_total_return"]),
  observation_count: z.number().int().positive(),
  first_observation_date: z.string(),
  latest_observation_date: z.string(),
});

const benchmarkPerformanceSchema = z.object({
  instrument_id: z.string(),
  display_name: z.string(),
  benchmark_family: z.string(),
  provider: z.literal("nifty_indices"),
  instrument_type: benchmarkSeriesSchema.shape.instrument_type,
  return_basis: benchmarkSeriesSchema.shape.return_basis,
  status: z.enum(["available", "stale_endpoint", "insufficient_history"]),
  reason_detail: z.string().nullable(),
  requested_as_of_date: z.string(),
  target_start_date: z.string(),
  endpoint_tolerance_days: z.number().int().nonnegative(),
  start_date: z.string().nullable(),
  end_date: z.string().nullable(),
  start_value: z.string().nullable(),
  end_value: z.string().nullable(),
  elapsed_days: z.number().int().positive().nullable(),
  endpoint_staleness_days: z.number().int().nonnegative().nullable(),
  total_return_pct: z.string().nullable(),
  annualized_return_pct: z.string().nullable(),
  day_count_convention: z.literal("actual/365"),
});

const heatmapPeriodSchema = z.enum([
  "1m",
  "3m",
  "6m",
  "1y",
  "3y",
  "5y",
  "10y",
  "rolling_1y",
  "rolling_3y",
  "rolling_5y",
  "rolling_10y",
]);

const heatmapTileSchema = z.object({
  tile_id: z.string(),
  label: z.string(),
  status: z.enum(["available", "stale_endpoint", "insufficient_history"]),
  reason_detail: z.string().nullable(),
  value_pct: z.string().nullable(),
  minimum_constituent_pct: z.string().nullable(),
  maximum_constituent_pct: z.string().nullable(),
  candidate_count: z.number().int().nonnegative(),
  constituent_count: z.number().int().nonnegative(),
  excluded_count: z.number().int().nonnegative(),
  excluded_stale_endpoint: z.number().int().nonnegative(),
  excluded_insufficient_history: z.number().int().nonnegative(),
  sample_count: z.number().int().nonnegative(),
  return_basis: z.enum(["nav_only", "price", "gross_total_return", "net_total_return"]),
  structure_type: z.enum(["open_ended", "close_ended", "interval", "other"]).nullable(),
  product_type: z.enum(["mutual_fund", "index_fund", "etf", "mixed"]).nullable(),
  classification_mapping_version: z.string().nullable(),
  period_start_date_min: z.string().nullable(),
  period_start_date_max: z.string().nullable(),
  period_end_date_min: z.string().nullable(),
  period_end_date_max: z.string().nullable(),
  maximum_endpoint_staleness_days: z.number().int().nonnegative().nullable(),
});

const heatmapSchema = z.object({
  universe: z.enum(["funds", "benchmarks", "indices"]),
  period: heatmapPeriodSchema,
  mode: z.enum(["trailing", "rolling"]),
  requested_as_of_date: z.string(),
  target_start_date: z.string().nullable(),
  endpoint_tolerance_days: z.number().int().nonnegative(),
  metric: z.enum([
    "median_constituent_return_pct",
    "series_return_pct",
    "median_rolling_annualized_return_pct",
  ]),
  aggregation_method: z.string(),
  observation_frequency: z.string(),
  day_count_convention: z.literal("actual/365"),
  distribution_treatment: z.string(),
  rolling_start_rule: z.string().nullable(),
  current_universe_limitation: z.string(),
  tiles: z.array(heatmapTileSchema),
});

const fundComparisonSchema = z.object({
  start_date: z.string(),
  end_date: z.string(),
  normalization_base: z.literal("first_observation_100"),
  return_basis: z.literal("nav_only"),
  distribution_markers: z.literal("record_date"),
  series: z.array(z.object({
    amfi_scheme_code: z.string(),
    scheme_name: z.string(),
    fund_house_name: z.string(),
    scheme_classification: z.string(),
    plan_type: z.string(),
    option_type: z.string(),
    points: z.array(z.object({
      nav_date: z.string(),
      nav_value: z.string(),
      normalized_value: z.string(),
    })),
    distributions: z.array(z.object({
      record_date: z.string(),
      amount_per_unit_inr: z.string(),
    })),
  })),
});

const navReturnSchema = z.object({
  start_date: z.string(),
  end_date: z.string(),
  start_nav: z.string(),
  end_nav: z.string(),
  elapsed_days: z.number().int().nonnegative(),
  total_return_pct: z.string(),
  annualized_return_pct: z.string().nullable(),
});

const rollingReturnSummarySchema = z.object({
  window_years: z.number().int().positive(),
  sample_count: z.number().int().nonnegative(),
  latest: navReturnSchema.nullable(),
  minimum_annualized_return_pct: z.string().nullable(),
  median_annualized_return_pct: z.string().nullable(),
  mean_annualized_return_pct: z.string().nullable(),
  maximum_annualized_return_pct: z.string().nullable(),
  positive_periods_pct: z.string().nullable(),
});

const schemePerformanceSchema = z.object({
  amfi_scheme_code: z.string(),
  return_basis: z.literal("nav_only"),
  distribution_treatment: z.literal("excluded"),
  day_count_convention: z.literal("actual/365"),
  rolling_start_rule: z.string(),
  observation_count: z.number().int().positive(),
  since_inception: navReturnSchema,
  rolling_returns: z.array(rollingReturnSummarySchema),
  drawdown: z.object({
    maximum_drawdown_pct: z.string(),
    peak_date: z.string(),
    trough_date: z.string(),
  }),
});

const distributionSourceSchema = z.object({
  provider: z.string(),
  source_kind: z.enum([
    "amfi_distribution_api",
    "amc_distribution_notice",
    "rta_distribution_history",
    "third_party_distribution_history",
  ]),
  source_record_id: z.string(),
  mutual_fund_id: z.string().nullable(),
  source_scheme_id: z.string().nullable(),
  source_option_id: z.string(),
  scheme_name: z.string(),
  nav_name: z.string(),
  record_date: z.string(),
  raw_source_value: z.string(),
  source_unit: z.string(),
  source_content_signature: z.string(),
  ingestion_batch_id: z.string(),
  source_url: z.string(),
  retrieved_at: z.string(),
  parser_version: z.string(),
  artifact_sha256: z.string().nullable(),
  identity_evidence_batch_id: z.string().nullable(),
  identity_evidence_url: z.string().nullable(),
  identity_artifact_sha256: z.string().nullable(),
  identity_evidence_details: z.string().nullable(),
});

const distributionEventBrowserSchema = z.object({
  items: z.array(z.object({
    event_id: z.string(),
    record_date: z.string(),
    event_type: z.literal("idcw_cash"),
    revisions: z.array(z.object({
      revision_id: z.string(),
      revision_number: z.number().int().positive(),
      amount_per_unit_inr: z.string(),
      is_current: z.boolean(),
      normalization_version: z.string(),
      normalized_at: z.string(),
      sources: z.array(distributionSourceSchema),
    })),
  })),
  total: z.number().int().nonnegative(),
  limit: z.number().int().positive(),
  offset: z.number().int().nonnegative(),
  coverage: z.object({
    assessment_run_id: z.string(),
    assessment_version: z.string(),
    coverage_status: z.enum(["events_present", "blocked_source_rows", "unverified_empty"]),
    source_row_count: z.number().int().nonnegative(),
    canonical_source_row_count: z.number().int().nonnegative(),
    blocked_source_row_count: z.number().int().nonnegative(),
    canonical_event_count: z.number().int().nonnegative(),
    first_source_record_date: z.string().nullable(),
    last_source_record_date: z.string().nullable(),
    assessed_at: z.string(),
  }).nullable(),
});

export type FundHouse = z.infer<typeof fundHouseSchema>;
export type SchemeCategory = z.infer<typeof schemeCategorySchema>;
export type ScreenerClassification = z.infer<typeof screenerClassificationSchema>;
export type SchemeStructure = ScreenerClassification["structure_type"];
export type SchemeProductType = ScreenerClassification["product_type"];
export type ClassificationAlias = z.infer<typeof classificationAliasSchema>;
export type ClassificationAliasSource = z.infer<typeof classificationAliasSourceSchema>;
export type ClassificationAliasManagement = z.infer<typeof classificationAliasManagementSchema>;
export type SchemeBrowserItem = z.infer<typeof schemeBrowserItemSchema>;
export type SchemeBrowser = z.infer<typeof schemeBrowserSchema>;
export type SchemePerformance = z.infer<typeof schemePerformanceSchema>;
export type DistributionEventBrowser = z.infer<typeof distributionEventBrowserSchema>;
export type ScreenerFund = z.infer<typeof screenerFundSchema>;
export type FundScreener = z.infer<typeof fundScreenerSchema>;
export type FundComparison = z.infer<typeof fundComparisonSchema>;
export type ScreenerHorizon = FundScreener["horizon"];
export type ScreenerOptionType = ScreenerFund["option_type"];
export type BenchmarkSeries = z.infer<typeof benchmarkSeriesSchema>;
export type BenchmarkPerformance = z.infer<typeof benchmarkPerformanceSchema>;
export type Heatmap = z.infer<typeof heatmapSchema>;
export type HeatmapPeriod = z.infer<typeof heatmapPeriodSchema>;
export type HeatmapTile = z.infer<typeof heatmapTileSchema>;
export type HeatmapUniverse = Heatmap["universe"];

export type SchemeFilters = {
  fundHouseId: string;
  categoryId: string;
  planType: string;
  optionType: string;
  search: string;
  limit: number;
  offset: number;
};

async function requestJson(url: string, init?: RequestInit): Promise<unknown> {
  const response = await fetch(`${apiBaseUrl}${url}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || `Request failed with HTTP ${response.status}`);
  }
  return response.json();
}

export async function listIngestionBatches(): Promise<IngestionBatch[]> {
  return z
    .array(ingestionBatchSchema)
    .parse(await requestJson("/ingestion-batches?limit=50&offset=0"));
}

export async function getDataCoverage(): Promise<DataCoverage> {
  return dataCoverageSchema.parse(await requestJson("/data/coverage"));
}

export async function listFundHouses(): Promise<FundHouse[]> {
  return z.array(fundHouseSchema).parse(await requestJson("/data/fund-houses"));
}

export async function listSchemeCategories(fundHouseId: string): Promise<SchemeCategory[]> {
  return z
    .array(schemeCategorySchema)
    .parse(await requestJson(`/data/fund-houses/${encodeURIComponent(fundHouseId)}/categories`));
}

export async function listClassifications(
  planType: "direct" | "regular",
  optionType: ScreenerOptionType,
  horizon: ScreenerHorizon,
  fundHouse: string,
): Promise<ScreenerClassification[]> {
  const parameters = new URLSearchParams({
    plan_type: planType,
    option_type: optionType,
    horizon,
  });
  if (fundHouse) parameters.append("fund_house", fundHouse);
  return z.array(screenerClassificationSchema).parse(
    await requestJson(`/data/classifications?${parameters}`),
  );
}

export async function listBenchmarkSeries(): Promise<BenchmarkSeries[]> {
  return z.array(benchmarkSeriesSchema).parse(await requestJson("/data/benchmark-series"));
}

export async function getBenchmarkPerformance(
  instrumentId: string,
  horizon: ScreenerHorizon,
  asOf: string,
  endpointToleranceDays: number,
): Promise<BenchmarkPerformance> {
  const parameters = new URLSearchParams({
    horizon,
    as_of: asOf,
    endpoint_tolerance_days: String(endpointToleranceDays),
  });
  return benchmarkPerformanceSchema.parse(
    await requestJson(
      `/data/benchmark-series/${encodeURIComponent(instrumentId)}/performance?${parameters}`,
    ),
  );
}

export async function getHeatmap(filters: {
  universe: HeatmapUniverse;
  period: HeatmapPeriod;
  planType: "direct" | "regular";
}): Promise<Heatmap> {
  const parameters = new URLSearchParams({
    universe: filters.universe,
    period: filters.period,
    plan_type: filters.planType,
  });
  return heatmapSchema.parse(await requestJson(`/data/heatmap?${parameters}`));
}

export async function getClassificationAliases(): Promise<ClassificationAliasManagement> {
  return classificationAliasManagementSchema.parse(
    await requestJson("/data/classification-aliases"),
  );
}

export type ClassificationAliasInput = {
  name: string;
  structure_type: SchemeStructure;
  status: "active" | "inactive";
  classification_ids: string[];
  reason: string;
};

export async function createClassificationAlias(
  input: ClassificationAliasInput,
): Promise<ClassificationAlias> {
  return classificationAliasSchema.parse(
    await requestJson("/data/classification-aliases", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  );
}

export async function updateClassificationAlias(
  aliasId: string,
  expectedVersion: number,
  input: ClassificationAliasInput,
): Promise<ClassificationAlias> {
  return classificationAliasSchema.parse(
    await requestJson(`/data/classification-aliases/${encodeURIComponent(aliasId)}`, {
      method: "PUT",
      body: JSON.stringify({ ...input, expected_version: expectedVersion }),
    }),
  );
}

export async function screenFunds(filters: {
  classificationId: string;
  horizon: ScreenerHorizon;
  planType: "direct" | "regular";
  optionType: ScreenerOptionType;
  fundHouse: string;
  search: string;
  limit: number;
  offset: number;
  exclusionLimit: number;
  exclusionOffset: number;
}): Promise<FundScreener> {
  const parameters = new URLSearchParams({
    classification_id: filters.classificationId,
    horizon: filters.horizon,
    plan_type: filters.planType,
    option_type: filters.optionType,
    limit: String(filters.limit),
    offset: String(filters.offset),
    exclusion_limit: String(filters.exclusionLimit),
    exclusion_offset: String(filters.exclusionOffset),
  });
  if (filters.fundHouse) parameters.append("fund_house", filters.fundHouse);
  if (filters.search.trim()) parameters.set("search", filters.search.trim());
  return fundScreenerSchema.parse(await requestJson(`/data/screener?${parameters}`));
}

export async function compareFunds(
  schemeCodes: string[],
  startDate: string,
  endDate: string,
): Promise<FundComparison> {
  const parameters = new URLSearchParams({ start_date: startDate, end_date: endDate });
  schemeCodes.forEach((code) => parameters.append("scheme_code", code));
  return fundComparisonSchema.parse(
    await requestJson(`/data/fund-comparison?${parameters}`),
  );
}

export async function listSchemes(filters: SchemeFilters): Promise<SchemeBrowser> {
  const parameters = new URLSearchParams({
    fund_house_id: filters.fundHouseId,
    limit: String(filters.limit),
    offset: String(filters.offset),
  });
  if (filters.categoryId) parameters.set("category_id", filters.categoryId);
  if (filters.planType) parameters.set("plan_type", filters.planType);
  if (filters.optionType) parameters.set("option_type", filters.optionType);
  if (filters.search.trim()) parameters.set("search", filters.search.trim());
  return schemeBrowserSchema.parse(await requestJson(`/data/schemes?${parameters}`));
}

export async function getSchemePerformance(amfiSchemeCode: string): Promise<SchemePerformance> {
  return schemePerformanceSchema.parse(
    await requestJson(`/data/schemes/${encodeURIComponent(amfiSchemeCode)}/performance`),
  );
}

export async function listSchemeDistributions(
  amfiSchemeCode: string,
): Promise<DistributionEventBrowser> {
  return distributionEventBrowserSchema.parse(
    await requestJson(
      `/data/schemes/${encodeURIComponent(amfiSchemeCode)}/distributions?limit=50&offset=0`,
    ),
  );
}
