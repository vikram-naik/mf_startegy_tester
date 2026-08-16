import type { ChangeEvent, FormEvent } from "react";

import type { StrategyFormValues } from "../model/strategy";

interface StrategyFormProps {
  values: StrategyFormValues;
  isSaving: boolean;
  isRevision: boolean;
  onChange: (values: StrategyFormValues) => void;
  onSubmit: () => void;
  onReset: () => void;
}

export function StrategyForm({
  values,
  isSaving,
  isRevision,
  onChange,
  onSubmit,
  onReset,
}: StrategyFormProps) {
  const setText = (field: keyof StrategyFormValues) => (event: ChangeEvent<HTMLInputElement>) =>
    onChange({ ...values, [field]: event.target.value });
  const setNumber =
    (field: "windowPeriods" | "maximumHoldings" | "navLag") =>
    (event: ChangeEvent<HTMLInputElement>) =>
      onChange({ ...values, [field]: Number(event.target.value) });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onSubmit();
  };

  return (
    <form className="builder" onSubmit={submit}>
      <div className="section-heading">
        <div>
          <span className="eyebrow">Strategy document</span>
          <h2>{isRevision ? "Edit strategy revision" : "Compose a strategy"}</h2>
        </div>
        {isRevision && (
          <button className="text-button" type="button" onClick={onReset}>
            New strategy
          </button>
        )}
      </div>

      <section className="form-section">
        <div className="section-index">01</div>
        <div className="section-fields">
          <h3>Identity & research window</h3>
          <label className="full-field">
            Strategy name
            <input required value={values.name} onChange={setText("name")} />
          </label>
          <label className="full-field">
            Research note
            <input value={values.description} onChange={setText("description")} />
          </label>
          <div className="field-grid">
            <label>
              Start date
              <input type="date" required value={values.startDate} onChange={setText("startDate")} />
            </label>
            <label>
              End date
              <input type="date" required value={values.endDate} onChange={setText("endDate")} />
            </label>
          </div>
        </div>
      </section>

      <section className="form-section">
        <div className="section-index">02</div>
        <div className="section-fields">
          <h3>Universe & ranking</h3>
          <label className="full-field">
            AMFI category
            <input required value={values.category} onChange={setText("category")} />
          </label>
          <div className="field-grid three">
            <label>
              Plan
              <select
                value={values.plan}
                onChange={(event) =>
                  onChange({ ...values, plan: event.target.value as StrategyFormValues["plan"] })
                }
              >
                <option value="direct">Direct</option>
                <option value="regular">Regular</option>
              </select>
            </label>
            <label>
              Option
              <select
                value={values.option}
                onChange={(event) =>
                  onChange({ ...values, option: event.target.value as StrategyFormValues["option"] })
                }
              >
                <option value="growth">Growth</option>
                <option value="idcw">IDCW</option>
              </select>
            </label>
            <label>
              Signal
              <select
                value={values.metric}
                onChange={(event) =>
                  onChange({ ...values, metric: event.target.value as StrategyFormValues["metric"] })
                }
              >
                <option value="trailing_return">Trailing return</option>
                <option value="volatility">Volatility</option>
                <option value="max_drawdown">Maximum drawdown</option>
                <option value="downside_deviation">Downside deviation</option>
                <option value="moving_average">Moving average</option>
              </select>
            </label>
          </div>
          <div className="field-grid">
            <label>
              Lookback observations
              <input type="number" min="2" value={values.windowPeriods} onChange={setNumber("windowPeriods")} />
            </label>
            <label>
              Maximum holdings
              <input type="number" min="1" value={values.maximumHoldings} onChange={setNumber("maximumHoldings")} />
            </label>
          </div>
        </div>
      </section>

      <section className="form-section">
        <div className="section-index">03</div>
        <div className="section-fields">
          <h3>Portfolio & execution</h3>
          <div className="field-grid three">
            <label>
              Allocation
              <select
                value={values.allocationMethod}
                onChange={(event) =>
                  onChange({
                    ...values,
                    allocationMethod: event.target.value as StrategyFormValues["allocationMethod"],
                  })
                }
              >
                <option value="equal_weight">Equal weight</option>
                <option value="score_weighted">Score weighted</option>
                <option value="inverse_volatility">Inverse volatility</option>
              </select>
            </label>
            <label>
              Rebalance
              <select
                value={values.rebalanceFrequency}
                onChange={(event) =>
                  onChange({
                    ...values,
                    rebalanceFrequency: event.target.value as StrategyFormValues["rebalanceFrequency"],
                  })
                }
              >
                <option value="monthly">Monthly</option>
                <option value="quarterly">Quarterly</option>
                <option value="half_yearly">Half-yearly</option>
                <option value="yearly">Yearly</option>
              </select>
            </label>
            <label>
              NAV lag (valuation days)
              <input type="number" min="0" max="30" value={values.navLag} onChange={setNumber("navLag")} />
            </label>
          </div>
          <div className="field-grid three">
            <label>
              Initial capital (INR)
              <input inputMode="decimal" required value={values.initialInvestment} onChange={setText("initialInvestment")} />
            </label>
            <label>
              Contribution (INR)
              <input inputMode="decimal" required value={values.contributionAmount} onChange={setText("contributionAmount")} />
            </label>
            <label>
              Contribution frequency
              <select
                value={values.contributionFrequency}
                onChange={(event) =>
                  onChange({
                    ...values,
                    contributionFrequency: event.target.value as StrategyFormValues["contributionFrequency"],
                  })
                }
              >
                <option value="none">None</option>
                <option value="monthly">Monthly</option>
                <option value="quarterly">Quarterly</option>
                <option value="yearly">Yearly</option>
              </select>
            </label>
          </div>
          <label className="full-field">
            Benchmark total-return identifier
            <input value={values.benchmarkIdentifier} onChange={setText("benchmarkIdentifier")} />
          </label>
        </div>
      </section>

      <div className="form-actions">
        <p>Saving creates an immutable, auditable version. It does not run a backtest yet.</p>
        <button className="primary-button" disabled={isSaving} type="submit">
          {isSaving ? "Saving…" : isRevision ? "Save new revision" : "Save strategy"}
        </button>
      </div>
    </form>
  );
}
