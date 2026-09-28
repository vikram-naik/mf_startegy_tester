import { useState } from "react";

import { ClassificationAliasWorkspace } from "./components/ClassificationAliasWorkspace";
import { DataWorkspace } from "./components/DataWorkspace";
import { HeatmapWorkspace } from "./components/HeatmapWorkspace";
import { ScreenerWorkspace } from "./components/ScreenerWorkspace";

type Workspace = "screener" | "heatmaps" | "data" | "aliases";

function initialWorkspace(): Workspace {
  const view = new URLSearchParams(window.location.search).get("view");
  return view === "heatmaps" || view === "data" || view === "aliases" ? view : "screener";
}

export default function App() {
  const [workspace, setWorkspace] = useState<Workspace>(initialWorkspace);

  const changeWorkspace = (next: Workspace) => {
    setWorkspace(next);
    const url = new URL(window.location.href);
    if (next !== "screener") url.searchParams.set("view", next);
    else url.searchParams.delete("view");
    window.history.replaceState(null, "", url);
  };

  return (
    <div className="app-shell">
      <header className="topbar">
        <button className="brand" type="button" onClick={() => changeWorkspace("screener")} aria-label="MF Fund Screener home">
          <span className="brand-mark">MF</span>
          <span>
            Fund <i>Screener</i>
          </span>
        </button>
        <nav className="primary-nav" aria-label="Primary navigation">
          <button className={workspace === "screener" ? "active" : ""} type="button" onClick={() => changeWorkspace("screener")}>Screener</button>
          <button className={workspace === "heatmaps" ? "active" : ""} type="button" onClick={() => changeWorkspace("heatmaps")}>Heatmaps</button>
          <button className={workspace === "data" ? "active" : ""} type="button" onClick={() => changeWorkspace("data")}>Data</button>
          <button className={workspace === "aliases" ? "active" : ""} type="button" onClick={() => changeWorkspace("aliases")}>Aliases</button>
        </nav>
        <div className="local-badge"><span /> Local workspace</div>
      </header>

      <main>
        {workspace === "screener" ? <ScreenerWorkspace /> : null}
        {workspace === "heatmaps" ? <HeatmapWorkspace /> : null}
        {workspace === "data" ? <DataWorkspace /> : null}
        {workspace === "aliases" ? <ClassificationAliasWorkspace /> : null}
      </main>
    </div>
  );
}
