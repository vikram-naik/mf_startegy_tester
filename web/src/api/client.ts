import { z } from "zod";

import { strategyDefinitionSchema, type StrategyDefinition } from "../model/strategy";

const apiBaseUrl =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "") ?? "http://127.0.0.1:8000/api/v1";

const strategyVersionSchema = z.object({
  id: z.string(),
  version: z.number().int().positive(),
  definition: strategyDefinitionSchema,
  created_at: z.string(),
});

const strategySummarySchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string(),
  latest_version: z.number().int().positive(),
  created_at: z.string(),
  updated_at: z.string(),
});

const strategyDetailSchema = strategySummarySchema.extend({
  versions: z.array(strategyVersionSchema),
});

export type StrategySummary = z.infer<typeof strategySummarySchema>;
export type StrategyDetail = z.infer<typeof strategyDetailSchema>;

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
  classification: z.string(),
  scheme_options: z.number().int().nonnegative(),
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

export type FundHouse = z.infer<typeof fundHouseSchema>;
export type SchemeCategory = z.infer<typeof schemeCategorySchema>;
export type SchemeBrowserItem = z.infer<typeof schemeBrowserItemSchema>;
export type SchemeBrowser = z.infer<typeof schemeBrowserSchema>;

export type SchemeFilters = {
  fundHouseId: string;
  category: string;
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

export async function listStrategies(): Promise<StrategySummary[]> {
  return z.array(strategySummarySchema).parse(await requestJson("/strategies"));
}

export async function getStrategy(strategyId: string): Promise<StrategyDetail> {
  return strategyDetailSchema.parse(await requestJson(`/strategies/${strategyId}`));
}

export async function saveStrategy(
  definition: StrategyDefinition,
  strategyId: string | null,
): Promise<StrategyDetail> {
  const body = JSON.stringify({ definition });
  if (strategyId === null) {
    return strategyDetailSchema.parse(
      await requestJson("/strategies", { method: "POST", body }),
    );
  }
  await requestJson(`/strategies/${strategyId}/versions`, { method: "POST", body });
  return getStrategy(strategyId);
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

export async function listSchemes(filters: SchemeFilters): Promise<SchemeBrowser> {
  const parameters = new URLSearchParams({
    fund_house_id: filters.fundHouseId,
    limit: String(filters.limit),
    offset: String(filters.offset),
  });
  if (filters.category) parameters.set("category", filters.category);
  if (filters.planType) parameters.set("plan_type", filters.planType);
  if (filters.optionType) parameters.set("option_type", filters.optionType);
  if (filters.search.trim()) parameters.set("search", filters.search.trim());
  return schemeBrowserSchema.parse(await requestJson(`/data/schemes?${parameters}`));
}
