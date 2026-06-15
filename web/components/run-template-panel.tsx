"use client";

import { useEffect, useState } from "react";
import type { AnalysisKind } from "@/lib/api";
import {
  deleteRunTemplate,
  getRunTemplates,
  saveRunTemplate,
  type RunTemplate
} from "@/lib/run-templates";

interface RunTemplatePanelProps<TFields extends object> {
  kind: AnalysisKind;
  currentFields: TFields;
  defaultName: string;
  describeTemplate: (template: RunTemplate<TFields>) => string[];
  mergeFieldsForUpdate?: (currentFields: TFields, existingFields: TFields) => TFields;
  onApply: (template: RunTemplate<TFields>) => void;
}

export function RunTemplatePanel<TFields extends object>({
  kind,
  currentFields,
  defaultName,
  describeTemplate,
  mergeFieldsForUpdate,
  onApply
}: RunTemplatePanelProps<TFields>) {
  const [templates, setTemplates] = useState<RunTemplate<TFields>[]>([]);
  const [name, setName] = useState(defaultName);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => {
    setTemplates(getRunTemplates<TFields>(kind));
  }, [kind]);

  function onSave() {
    const existing = selectedId
      ? templates.find((template) => template.id === selectedId)
      : undefined;
    const fields =
      existing && mergeFieldsForUpdate
        ? mergeFieldsForUpdate(currentFields, existing.fields)
        : currentFields;
    const template = saveRunTemplate(kind, name, fields, selectedId ?? undefined);
    setSelectedId(template.id);
    setName(template.name);
    setTemplates(getRunTemplates<TFields>(kind));
  }

  function onDelete(templateId: string) {
    deleteRunTemplate(templateId);
    if (selectedId === templateId) {
      setSelectedId(null);
      setName(defaultName);
    }
    setTemplates(getRunTemplates<TFields>(kind));
  }

  function onApplyTemplate(template: RunTemplate<TFields>) {
    setSelectedId(template.id);
    setName(template.name);
    onApply(template);
  }

  return (
    <section className="template-panel">
      <div className="template-head">
        <div>
          <h3>Run templates</h3>
          <p>Save parameters and file identities for edited reruns.</p>
        </div>
      </div>
      <div className="field">
        <label htmlFor={`${kind}-template-name`}>Template name</label>
        <input
          id={`${kind}-template-name`}
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </div>
      <button className="button primary" type="button" onClick={onSave}>
        {selectedId ? "Update template" : "Save as template"}
      </button>
      <p className="subtle">
        Uploaded files are saved as names only. Reattach those files before previewing a loaded
        template.
      </p>
      {templates.length ? (
        <div className="template-list">
          {templates.map((template) => (
            <article className="template-item" key={template.id}>
              <div>
                <strong>{template.name}</strong>
                <span>{new Date(template.updated_at).toLocaleString()}</span>
              </div>
              <ul>
                {describeTemplate(template).map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
              <div className="button-row">
                <button
                  className="button secondary compact"
                  type="button"
                  onClick={() => onApplyTemplate(template)}
                >
                  Run with edits
                </button>
                <button
                  className="button danger compact"
                  type="button"
                  onClick={() => onDelete(template.id)}
                >
                  Delete
                </button>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <p className="subtle">No saved templates for this analysis yet.</p>
      )}
    </section>
  );
}
