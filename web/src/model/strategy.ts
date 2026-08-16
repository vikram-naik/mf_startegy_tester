import { z } from "zod";

const decimalSchema = z.string().regex(/^\d+(\.\d+)?$/);

export const strategyDefinitionSchema = z.object({
  schema_version: z.literal("1.0"),
  name: z.string().min(1),
  description: z.string(),
  date_range: z.object({ start: z.iso.date(), end: z.iso.date() }),
  universe: z.object({
    scheme_codes: z.array(z.string()),
    amc_names: z.array(z.string()),
    categories: z.array(z.string()),
    plan: z.enum(["direct", "regular"]),
    option: z.enum(["growth", "idcw"]),
    require_active_at_decision_time: z.boolean(),
    minimum_history_periods: z.number().int().nonnegative(),
  }),
  signals: z.array(
    z.object({
      id: z.string(),
      metric: z.enum([
        "trailing_return",
        "volatility",
        "max_drawdown",
        "downside_deviation",
        "moving_average",
      ]),
      window_periods: z.number().int().min(2),
      direction: z.enum(["ascending", "descending"]),
      weight: decimalSchema,
    }),
  ),
  selection: z.object({ rank_by: z.array(z.string()), maximum_holdings: z.number().int() }),
  allocation: z.object({
    method: z.enum(["equal_weight", "score_weighted", "inverse_volatility", "fixed_weight"]),
    maximum_weight: decimalSchema,
    fixed_weights: z.record(z.string(), decimalSchema),
  }),
  rebalance: z.object({
    frequency: z.enum(["monthly", "quarterly", "half_yearly", "yearly"]),
    calendar_day: z.number().int(),
  }),
  execution: z.object({
    decision_time: z.enum(["before_cutoff", "after_cutoff"]),
    nav_lag_valuation_days: z.number().int(),
    missing_nav_policy: z.enum(["fail", "next_available", "skip_order"]),
    maximum_nav_wait_days: z.number().int(),
  }),
  cash_flows: z.object({
    initial_investment: decimalSchema,
    contribution_amount: decimalSchema,
    contribution_frequency: z.enum(["none", "monthly", "quarterly", "yearly"]),
  }),
  costs: z.object({
    exit_load_rate: decimalSchema,
    transaction_cost_rate: decimalSchema,
    taxes_included: z.boolean(),
    expense_ratio_already_reflected_in_nav: z.literal(true),
  }),
  benchmark: z
    .object({
      identifier: z.string(),
      series_type: z.enum(["price_return", "total_return"]),
      currency: z.literal("INR"),
    })
    .nullable(),
});

export type StrategyDefinition = z.infer<typeof strategyDefinitionSchema>;

export interface StrategyFormValues {
  name: string;
  description: string;
  startDate: string;
  endDate: string;
  category: string;
  plan: "direct" | "regular";
  option: "growth" | "idcw";
  metric: StrategyDefinition["signals"][number]["metric"];
  windowPeriods: number;
  maximumHoldings: number;
  allocationMethod: Exclude<StrategyDefinition["allocation"]["method"], "fixed_weight">;
  rebalanceFrequency: StrategyDefinition["rebalance"]["frequency"];
  initialInvestment: string;
  contributionAmount: string;
  contributionFrequency: StrategyDefinition["cash_flows"]["contribution_frequency"];
  navLag: number;
  benchmarkIdentifier: string;
}

export const defaultFormValues: StrategyFormValues = {
  name: "Monthly momentum portfolio",
  description: "Rank eligible direct-growth schemes and hold the strongest trailing performers.",
  startDate: "2020-01-01",
  endDate: "2025-12-31",
  category: "Equity: Flexi Cap",
  plan: "direct",
  option: "growth",
  metric: "trailing_return",
  windowPeriods: 252,
  maximumHoldings: 3,
  allocationMethod: "equal_weight",
  rebalanceFrequency: "monthly",
  initialInvestment: "100000",
  contributionAmount: "10000",
  contributionFrequency: "monthly",
  navLag: 1,
  benchmarkIdentifier: "NIFTY_500_TRI",
};

export function buildStrategyDefinition(values: StrategyFormValues): StrategyDefinition {
  const hasContribution = Number(values.contributionAmount) > 0;
  return strategyDefinitionSchema.parse({
    schema_version: "1.0",
    name: values.name,
    description: values.description,
    date_range: { start: values.startDate, end: values.endDate },
    universe: {
      scheme_codes: [],
      amc_names: [],
      categories: values.category ? [values.category] : [],
      plan: values.plan,
      option: values.option,
      require_active_at_decision_time: true,
      minimum_history_periods: values.windowPeriods,
    },
    signals: [
      {
        id: "primary_signal",
        metric: values.metric,
        window_periods: values.windowPeriods,
        direction: values.metric === "volatility" ? "ascending" : "descending",
        weight: "1",
      },
    ],
    selection: { rank_by: ["primary_signal"], maximum_holdings: values.maximumHoldings },
    allocation: {
      method: values.allocationMethod,
      maximum_weight: "1",
      fixed_weights: {},
    },
    rebalance: { frequency: values.rebalanceFrequency, calendar_day: 1 },
    execution: {
      decision_time: "after_cutoff",
      nav_lag_valuation_days: values.navLag,
      missing_nav_policy: "next_available",
      maximum_nav_wait_days: 7,
    },
    cash_flows: {
      initial_investment: values.initialInvestment,
      contribution_amount: hasContribution ? values.contributionAmount : "0",
      contribution_frequency: hasContribution ? values.contributionFrequency : "none",
    },
    costs: {
      exit_load_rate: "0",
      transaction_cost_rate: "0",
      taxes_included: false,
      expense_ratio_already_reflected_in_nav: true,
    },
    benchmark: values.benchmarkIdentifier
      ? {
          identifier: values.benchmarkIdentifier,
          series_type: "total_return",
          currency: "INR",
        }
      : null,
  });
}

export function formValuesFromDefinition(definition: StrategyDefinition): StrategyFormValues {
  const signal = definition.signals[0];
  if (signal === undefined || definition.allocation.method === "fixed_weight") {
    throw new Error("This strategy uses controls not yet exposed by the bootstrap editor.");
  }
  return {
    name: definition.name,
    description: definition.description,
    startDate: definition.date_range.start,
    endDate: definition.date_range.end,
    category: definition.universe.categories[0] ?? "",
    plan: definition.universe.plan,
    option: definition.universe.option,
    metric: signal.metric,
    windowPeriods: signal.window_periods,
    maximumHoldings: definition.selection.maximum_holdings,
    allocationMethod: definition.allocation.method,
    rebalanceFrequency: definition.rebalance.frequency,
    initialInvestment: definition.cash_flows.initial_investment,
    contributionAmount: definition.cash_flows.contribution_amount,
    contributionFrequency: definition.cash_flows.contribution_frequency,
    navLag: definition.execution.nav_lag_valuation_days,
    benchmarkIdentifier: definition.benchmark?.identifier ?? "",
  };
}
