import type { StrategySummary } from "../api/client";

interface StrategyLibraryProps {
  strategies: StrategySummary[];
  selectedId: string | null;
  onSelect: (strategyId: string) => void;
}

export function StrategyLibrary({ strategies, selectedId, onSelect }: StrategyLibraryProps) {
  return (
    <aside className="library">
      <div className="library-heading">
        <span className="eyebrow">Saved research</span>
        <span className="count">{strategies.length.toString().padStart(2, "0")}</span>
      </div>
      {strategies.length === 0 ? (
        <div className="empty-state">
          <div className="empty-mark">∿</div>
          <h3>No strategy versions yet</h3>
          <p>Your saved strategy documents will appear here. Market data is never inferred.</p>
        </div>
      ) : (
        <ul className="strategy-list">
          {strategies.map((strategy) => (
            <li key={strategy.id}>
              <button
                className={selectedId === strategy.id ? "strategy-card selected" : "strategy-card"}
                type="button"
                onClick={() => onSelect(strategy.id)}
              >
                <span className="version">v{strategy.latest_version}</span>
                <strong>{strategy.name}</strong>
                <span>{strategy.description || "No research note"}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="integrity-note">
        <span className="status-dot" />
        <div>
          <strong>Research integrity</strong>
          <p>Run results will remain locked to a strategy revision and data snapshot.</p>
        </div>
      </div>
    </aside>
  );
}
