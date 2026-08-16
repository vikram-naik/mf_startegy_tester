import { describe, expect, it } from "vitest";

import { buildStrategyDefinition, defaultFormValues, formValuesFromDefinition } from "./strategy";

describe("strategy document builder", () => {
  it("creates an explicit, versioned strategy document", () => {
    const definition = buildStrategyDefinition(defaultFormValues);

    expect(definition.schema_version).toBe("1.0");
    expect(definition.universe.plan).toBe("direct");
    expect(definition.execution.nav_lag_valuation_days).toBe(1);
    expect(definition.costs.expense_ratio_already_reflected_in_nav).toBe(true);
    expect(definition.benchmark?.series_type).toBe("total_return");
  });

  it("round-trips all controls exposed by the bootstrap editor", () => {
    const definition = buildStrategyDefinition(defaultFormValues);

    expect(formValuesFromDefinition(definition)).toEqual(defaultFormValues);
  });

  it("removes contribution frequency when contribution is zero", () => {
    const definition = buildStrategyDefinition({ ...defaultFormValues, contributionAmount: "0" });

    expect(definition.cash_flows.contribution_frequency).toBe("none");
  });
});
