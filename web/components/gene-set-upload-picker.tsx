"use client";

import { useState } from "react";

export interface GeneSetUploadFiles {
  gmtFile: File | null;
  oboFile: File | null;
  annotationFile: File | null;
}

interface GeneSetUploadPickerProps {
  idPrefix: string;
  title: string;
  description: string;
  gmtLabel: string;
  oboLabel: string;
  annotationLabel: string;
  collectionLabel: string;
  files: GeneSetUploadFiles;
  onChange: (files: GeneSetUploadFiles) => void;
}

export function GeneSetUploadPicker({
  idPrefix,
  title,
  description,
  gmtLabel,
  oboLabel,
  annotationLabel,
  collectionLabel,
  files,
  onChange
}: GeneSetUploadPickerProps) {
  const [gmtInputKey, setGmtInputKey] = useState(0);
  const [goInputKey, setGoInputKey] = useState(0);
  const usesGmt = files.gmtFile !== null;
  const usesGo = files.oboFile !== null || files.annotationFile !== null;

  function clearUploads() {
    onChange({ gmtFile: null, oboFile: null, annotationFile: null });
    setGmtInputKey((value) => value + 1);
    setGoInputKey((value) => value + 1);
  }

  function onGmtChange(file: File | null) {
    if (file) {
      onChange({ gmtFile: file, oboFile: null, annotationFile: null });
      setGoInputKey((value) => value + 1);
      return;
    }
    onChange({ ...files, gmtFile: null });
  }

  function onGoChange(kind: "obo" | "annotation", file: File | null) {
    const nextFiles = {
      gmtFile: file ? null : files.gmtFile,
      oboFile: kind === "obo" ? file : files.oboFile,
      annotationFile: kind === "annotation" ? file : files.annotationFile
    };
    onChange(nextFiles);
    if (file) {
      setGmtInputKey((value) => value + 1);
    }
  }

  return (
    <div className="upload-section">
      <div className="upload-section-head">
        <div>
          <h3>{title}</h3>
          <p>{description}</p>
        </div>
        {usesGmt || usesGo ? (
          <button className="button secondary compact" type="button" onClick={clearUploads}>
            Clear
          </button>
        ) : null}
      </div>
      <div className="file-grid three">
        <div className="field">
          <label htmlFor={`${idPrefix}-gmt-file`}>{gmtLabel}</label>
          <input
            key={`${idPrefix}-gmt-${gmtInputKey}`}
            id={`${idPrefix}-gmt-file`}
            accept=".gmt,.txt,.tsv"
            disabled={usesGo}
            type="file"
            onChange={(event) => onGmtChange(event.target.files?.[0] ?? null)}
          />
          <small>
            {usesGo ? `Disabled while GO/OBO ${collectionLabel} files are selected.` : ""}
          </small>
        </div>
        <div className="field">
          <label htmlFor={`${idPrefix}-obo-file`}>{oboLabel}</label>
          <input
            key={`${idPrefix}-obo-${goInputKey}`}
            id={`${idPrefix}-obo-file`}
            accept=".obo,.txt"
            disabled={usesGmt}
            type="file"
            onChange={(event) => onGoChange("obo", event.target.files?.[0] ?? null)}
          />
          <small>{usesGmt ? `Disabled while a ${collectionLabel} GMT is selected.` : ""}</small>
        </div>
        <div className="field">
          <label htmlFor={`${idPrefix}-annotation-file`}>{annotationLabel}</label>
          <input
            key={`${idPrefix}-annotation-${goInputKey}`}
            id={`${idPrefix}-annotation-file`}
            accept=".gaf,.gpad,.txt,.tsv,.csv"
            disabled={usesGmt}
            type="file"
            onChange={(event) => onGoChange("annotation", event.target.files?.[0] ?? null)}
          />
          <small>{usesGmt ? `Disabled while a ${collectionLabel} GMT is selected.` : ""}</small>
        </div>
      </div>
    </div>
  );
}
