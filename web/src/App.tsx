import { useEffect, useState } from "react";

import {
  getStrategy,
  listStrategies,
  saveStrategy,
  type StrategySummary,
} from "./api/client";
import { StrategyForm } from "./components/StrategyForm";
import { StrategyLibrary } from "./components/StrategyLibrary";
import { DataWorkspace } from "./components/DataWorkspace";
import {
  buildStrategyDefinition,
  defaultFormValues,
  formValuesFromDefinition,
  type StrategyFormValues,
} from "./model/strategy";

export default function App() {
  const initialView = new URLSearchParams(window.location.search).get("view") === "data"
    ? "data"
    : "build";
  const [view, setView] = useState<"build" | "data">(initialView);
  const [values, setValues] = useState<StrategyFormValues>(defaultFormValues);
  const [strategies, setStrategies] = useState<StrategySummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    listStrategies().then(setStrategies).catch((error: unknown) => {
      setMessage(error instanceof Error ? error.message : "Unable to load strategy catalog.");
    });
  }, []);

  const reset = () => {
    setSelectedId(null);
    setValues(defaultFormValues);
    setMessage(null);
  };

  const navigate = (nextView: "build" | "data") => {
    setView(nextView);
    const url = nextView === "data" ? "/?view=data" : "/";
    window.history.replaceState(null, "", url);
  };

  const selectStrategy = async (strategyId: string) => {
    try {
      const detail = await getStrategy(strategyId);
      const latest = detail.versions.at(-1);
      if (latest === undefined) throw new Error("Strategy has no saved definition.");
      setValues(formValuesFromDefinition(latest.definition));
      setSelectedId(strategyId);
      setMessage(`Loaded version ${latest.version}. Saving will create a new revision.`);
    } catch (error: unknown) {
      setMessage(error instanceof Error ? error.message : "Unable to load strategy.");
    }
  };

  const submit = async () => {
    setIsSaving(true);
    setMessage(null);
    try {
      const definition = buildStrategyDefinition(values);
      const saved = await saveStrategy(definition, selectedId);
      setSelectedId(saved.id);
      setStrategies(await listStrategies());
      setMessage(`Saved ${saved.name} as immutable version ${saved.latest_version}.`);
    } catch (error: unknown) {
      setMessage(error instanceof Error ? error.message : "Unable to save strategy.");
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="MF Strategy Lab home">
          <span className="brand-mark">MF</span>
          <span>
            Strategy <i>Lab</i>
          </span>
        </a>
        <nav aria-label="Primary navigation">
          <button className={view === "build" ? "active" : ""} onClick={() => navigate("build")}>Build</button>
          <button className={view === "data" ? "active" : ""} onClick={() => navigate("data")}>Data</button>
          <button disabled title="Backtest execution is planned for Phase 5">Runs</button>
        </nav>
        <div className="local-badge"><span /> Local workspace</div>
      </header>

      <main>
        <div className={view === "data" ? "hero compact" : "hero"}>
          <div>
            <span className="eyebrow">Point-in-time research workspace</span>
            <h1>{view === "build" ? <>Model the rule.<br /><em>Interrogate the result.</em></> : <>Trust begins with<br /><em>traceable inputs.</em></>}</h1>
          </div>
          <p>
            {view === "build"
              ? "Compose versioned mutual-fund strategies from explicit financial assumptions. No strategy-specific code and no hidden defaults."
              : "Inspect every retrieval, parser version, row count, failure, and immutable source checksum before using the data for research."}
          </p>
        </div>

        {view === "build" ? (
          <>
            {message && <div className="message" role="status">{message}</div>}
            <div className="workspace-grid">
              <StrategyForm
                values={values}
                isSaving={isSaving}
                isRevision={selectedId !== null}
                onChange={setValues}
                onSubmit={submit}
                onReset={reset}
              />
              <StrategyLibrary strategies={strategies} selectedId={selectedId} onSelect={selectStrategy} />
            </div>
          </>
        ) : <DataWorkspace />}
      </main>
    </div>
  );
}
