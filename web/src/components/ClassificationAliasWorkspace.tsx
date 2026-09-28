import { useEffect, useMemo, useState } from "react";

import {
  createClassificationAlias,
  getClassificationAliases,
  updateClassificationAlias,
  type ClassificationAlias,
  type ClassificationAliasInput,
  type ClassificationAliasManagement,
  type SchemeStructure,
} from "../api/client";
import { schemeStructureLabels } from "../model/classificationFilters";

type Draft = ClassificationAliasInput;

const emptyDraft: Draft = {
  name: "",
  structure_type: "open_ended",
  status: "active",
  classification_ids: [],
  reason: "",
};

export function ClassificationAliasWorkspace() {
  const [data, setData] = useState<ClassificationAliasManagement | null>(null);
  const [selectedId, setSelectedId] = useState<string>("new");
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const selectedAlias = data?.aliases.find((alias) => alias.id === selectedId) ?? null;
  const selectedRevisions = (data?.revisions ?? []).filter(
    (revision) => revision.alias_id === selectedAlias?.id,
  );

  const load = async (nextSelectedId?: string) => {
    setLoading(true);
    setError("");
    try {
      const response = await getClassificationAliases();
      setData(response);
      if (nextSelectedId) setSelectedId(nextSelectedId);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load aliases");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  useEffect(() => {
    if (selectedId === "new") {
      setDraft(emptyDraft);
      return;
    }
    const alias = data?.aliases.find((item) => item.id === selectedId);
    if (!alias) return;
    setDraft({
      name: alias.name,
      structure_type: alias.structure_type,
      status: alias.status,
      classification_ids: alias.classification_ids,
      reason: "",
    });
  }, [data, selectedId]);

  const availableSources = useMemo(
    () =>
      (data?.source_classifications ?? []).filter(
        (source) =>
          source.structure_type === draft.structure_type &&
          (source.alias_id === null || source.alias_id === selectedAlias?.id),
      ),
    [data, draft.structure_type, selectedAlias?.id],
  );

  const save = async () => {
    setMessage("");
    setError("");
    if (
      !draft.name.trim() ||
      draft.reason.trim().length < 3 ||
      (draft.status === "active" && draft.classification_ids.length === 0)
    ) {
      setError(
        "Name, a change reason of at least three characters, and at least one AMFI classification for an active alias are required.",
      );
      return;
    }
    setSaving(true);
    try {
      const saved = selectedAlias
        ? await updateClassificationAlias(selectedAlias.id, selectedAlias.version, draft)
        : await createClassificationAlias(draft);
      setMessage(`Saved ${saved.name} as version ${saved.version}.`);
      await load(saved.id);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Unable to save alias");
    } finally {
      setSaving(false);
    }
  };

  const changeStructure = (structure: SchemeStructure) => {
    setDraft({ ...draft, structure_type: structure, classification_ids: [] });
  };

  const changeStatus = (status: Draft["status"]) => {
    setDraft({
      ...draft,
      status,
      classification_ids: status === "inactive" ? [] : draft.classification_ids,
    });
  };

  const toggleClassification = (classificationId: string) => {
    const selected = new Set(draft.classification_ids);
    if (selected.has(classificationId)) selected.delete(classificationId);
    else selected.add(classificationId);
    setDraft({ ...draft, classification_ids: [...selected].sort() });
  };

  return (
    <section className="alias-workspace">
      <div className="page-heading">
        <div>
          <span className="eyebrow">Local classification layer</span>
          <h1>Classification aliases</h1>
        </div>
        <p>
          Give AMFI classifications concise screener names and group approved predecessor or
          successor labels. Raw AMFI text and its provenance are never changed.
        </p>
      </div>

      {error ? <div className="message alias-error">{error}</div> : null}
      {message ? <div className="message">{message}</div> : null}

      <div className="alias-layout">
        <aside className="alias-list" aria-label="Classification aliases">
          <div className="alias-list-heading">
            <strong>Aliases</strong>
            <button type="button" onClick={() => setSelectedId("new")}>New alias</button>
          </div>
          {loading ? <p className="alias-placeholder">Loading aliases…</p> : null}
          {!loading && data?.aliases.length === 0 ? (
            <p className="alias-placeholder">No aliases have been configured.</p>
          ) : null}
          {data?.aliases.map((alias) => (
            <button
              className={alias.id === selectedId ? "alias-row selected" : "alias-row"}
              key={alias.id}
              type="button"
              onClick={() => setSelectedId(alias.id)}
            >
              <span>{alias.name}</span>
              <small>
                {schemeStructureLabels[alias.structure_type]} · {alias.classification_ids.length}
                {alias.status === "inactive" ? " · inactive" : ""}
              </small>
            </button>
          ))}
        </aside>

        <div className="alias-editor">
          <div className="alias-editor-heading">
            <div>
              <span className="eyebrow">{selectedAlias ? `Version ${selectedAlias.version}` : "New mapping"}</span>
              <h2>{selectedAlias ? selectedAlias.name : "Create an alias"}</h2>
            </div>
            <span className="read-only-badge">AMFI source remains read only</span>
          </div>

          <div className="alias-form-grid">
            <label>
              Screener name
              <input
                maxLength={100}
                value={draft.name}
                onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                placeholder="Corporate Bond"
              />
            </label>
            <label>
              Scheme structure
              <select
                value={draft.structure_type}
                onChange={(event) => changeStructure(event.target.value as SchemeStructure)}
              >
                {(Object.keys(schemeStructureLabels) as SchemeStructure[]).map((structure) => (
                  <option key={structure} value={structure}>{schemeStructureLabels[structure]}</option>
                ))}
              </select>
            </label>
            <label>
              Status
              <select
                value={draft.status}
                onChange={(event) => changeStatus(event.target.value as Draft["status"])}
              >
                <option value="active">Active</option>
                <option value="inactive">Inactive</option>
              </select>
            </label>
          </div>

          <fieldset className="alias-source-picker" disabled={draft.status === "inactive"}>
            <legend>Mapped AMFI classifications</legend>
            {draft.status === "inactive" ? (
              <p>Saving an inactive alias releases all of its classifications for reassignment. Its prior mappings remain in the immutable audit history.</p>
            ) : availableSources.length === 0 ? (
              <p>No unassigned AMFI classifications are available for this structure.</p>
            ) : (
              availableSources.map((source) => (
                <label key={source.classification_id}>
                  <input
                    type="checkbox"
                    checked={draft.classification_ids.includes(source.classification_id)}
                    onChange={() => toggleClassification(source.classification_id)}
                  />
                  <span>
                    <strong>{source.amfi_classification}</strong>
                    <small>{source.raw_labels.length} retained raw label{source.raw_labels.length === 1 ? "" : "s"}</small>
                  </span>
                </label>
              ))
            )}
          </fieldset>

          <label className="alias-reason">
            Change reason
            <input
              maxLength={500}
              value={draft.reason}
              onChange={(event) => setDraft({ ...draft, reason: event.target.value })}
              placeholder="Reviewed AMFI predecessor/successor equivalence"
            />
          </label>

          <div className="alias-actions">
            <p>Saving creates an immutable audit revision and immediately changes future screener queries. Deactivation releases every mapped classification.</p>
            <button type="button" disabled={saving || loading} onClick={() => void save()}>
              {saving ? "Saving…" : "Save alias"}
            </button>
          </div>
          {selectedRevisions.length > 0 ? (
            <details className="alias-history">
              <summary>Audit history · {selectedRevisions.length} revision{selectedRevisions.length === 1 ? "" : "s"}</summary>
              {selectedRevisions.map((revision) => (
                <div key={`${revision.alias_id}-${revision.version}`}>
                  <strong>Version {revision.version} · {revision.name}</strong>
                  <span>{revision.created_at} · {revision.classification_ids.length} mappings</span>
                  <p>{revision.reason}</p>
                </div>
              ))}
            </details>
          ) : null}
        </div>
      </div>
    </section>
  );
}
