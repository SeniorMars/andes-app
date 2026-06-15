"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useRef, useState } from "react";
import { GeneSetUploadPicker } from "@/components/gene-set-upload-picker";
import { PreflightPreview } from "@/components/preflight-preview";
import { RunTemplatePanel } from "@/components/run-template-panel";
import type { JobPreview } from "@/lib/api";
import { previewTextJob, submitTextJob } from "@/lib/api";
import { benchmarkGseaExample } from "@/lib/examples";
import type {
  GeneSetCollectionTemplate,
  GoNamespace,
  GseaTemplateFields,
  RunTemplate
} from "@/lib/run-templates";

const GO_NAMESPACE_OPTIONS: { value: GoNamespace; label: string }[] = [
  { value: "biological_process", label: "Biological process" },
  { value: "molecular_function", label: "Molecular function" },
  { value: "cellular_component", label: "Cellular component" }
];

interface TemplateFileRequirement {
  key: string;
  label: string;
}

const TEMPLATE_FILE_FALLBACK_WARNING =
  "This template originally used uploaded files. If you continue without reattaching them, ANDES will use pasted text and/or the configured default collection instead.";
const TEMPLATE_TEXT_OMITTED_WARNING =
  "Large pasted input was not stored with this template. Re-enter or upload the input before previewing.";

function collectionIdentity(
  gmtFile: File | null,
  oboFile: File | null,
  annotationFile: File | null
): GeneSetCollectionTemplate {
  if (gmtFile) {
    return { mode: "gmt", gmtFileName: gmtFile.name };
  }
  if (oboFile || annotationFile) {
    return {
      mode: "go",
      oboFileName: oboFile?.name ?? null,
      annotationFileName: annotationFile?.name ?? null
    };
  }
  return { mode: "default" };
}

function describeCollection(collection: GeneSetCollectionTemplate): string {
  if (collection.mode === "gmt") {
    return `Collection: GMT ${collection.gmtFileName ?? "file"}`;
  }
  if (collection.mode === "go") {
    return `Collection: GO ${collection.oboFileName ?? "OBO file"} + ${
      collection.annotationFileName ?? "annotation file"
    }`;
  }
  return "Collection: configured default";
}

function fileNotice(fields: GseaTemplateFields): string {
  const files = [
    fields.rankedFileName ? `ranked file ${fields.rankedFileName}` : "",
    fields.geneSetCollection.mode === "gmt"
      ? `gene-set GMT ${fields.geneSetCollection.gmtFileName}`
      : "",
    fields.geneSetCollection.mode === "go"
      ? `gene-set GO files ${fields.geneSetCollection.oboFileName ?? "OBO"} and ${
          fields.geneSetCollection.annotationFileName ?? "annotations"
        }`
      : ""
  ].filter(Boolean);
  const notices = [
    fields.rankedTextOmitted ? TEMPLATE_TEXT_OMITTED_WARNING : "",
    files.length ? `${TEMPLATE_FILE_FALLBACK_WARNING} Reattach: ${files.join("; ")}.` : ""
  ].filter(Boolean);
  return notices.length ? notices.join(" ") : "Template applied.";
}

function collectionRequirements(collection: GeneSetCollectionTemplate): TemplateFileRequirement[] {
  if (collection.mode === "gmt") {
    return collection.gmtFileName
      ? [{ key: "gene_set_gmt", label: `gene-set GMT ${collection.gmtFileName}` }]
      : [];
  }
  if (collection.mode === "go") {
    return [
      collection.oboFileName
        ? { key: "gene_set_obo", label: `gene-set OBO ${collection.oboFileName}` }
        : null,
      collection.annotationFileName
        ? {
            key: "gene_set_annotation",
            label: `gene-set annotations ${collection.annotationFileName}`
          }
        : null
    ].filter((item): item is TemplateFileRequirement => item !== null);
  }
  return [];
}

function templateRequirements(fields: GseaTemplateFields): TemplateFileRequirement[] {
  return [
    fields.rankedFileName
      ? { key: "ranked_file", label: `ranked file ${fields.rankedFileName}` }
      : null,
    ...collectionRequirements(fields.geneSetCollection)
  ].filter((item): item is TemplateFileRequirement => item !== null);
}

function mergeCollectionIdentity(
  current: GeneSetCollectionTemplate,
  existing: GeneSetCollectionTemplate
): GeneSetCollectionTemplate {
  return current.mode === "default" && existing.mode !== "default" ? existing : current;
}

function mergeTemplateFileIdentity(
  current: GseaTemplateFields,
  existing: GseaTemplateFields
): GseaTemplateFields {
  return {
    ...current,
    rankedFileName: current.rankedFileName ?? existing.rankedFileName,
    geneSetCollection: mergeCollectionIdentity(
      current.geneSetCollection,
      existing.geneSetCollection
    )
  };
}

export default function GseaPage() {
  const router = useRouter();
  const [ranked, setRanked] = useState(benchmarkGseaExample);
  const [rankedFile, setRankedFile] = useState<File | null>(null);
  const [geneSetFile, setGeneSetFile] = useState<File | null>(null);
  const [geneSetOboFile, setGeneSetOboFile] = useState<File | null>(null);
  const [geneSetAnnotationFile, setGeneSetAnnotationFile] = useState<File | null>(null);
  const [minSize, setMinSize] = useState(10);
  const [maxSize, setMaxSize] = useState(300);
  const [goNamespace, setGoNamespace] = useState<GoNamespace>("biological_process");
  const [error, setError] = useState("");
  const [templateNotice, setTemplateNotice] = useState("");
  const [templateFileRequirements, setTemplateFileRequirements] = useState<
    TemplateFileRequirement[]
  >([]);
  const [confirmTemplateFileSkip, setConfirmTemplateFileSkip] = useState(false);
  const [fileInputResetKey, setFileInputResetKey] = useState(0);
  const [preview, setPreview] = useState<JobPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const previewAbortRef = useRef<AbortController | null>(null);
  const previewSeq = useRef(0);
  const rankedCount = rankedFile ? null : ranked.split(/\n+/).filter((line) => line.trim()).length;

  useEffect(() => {
    return () => {
      previewSeq.current += 1;
      previewAbortRef.current?.abort();
    };
  }, []);

  function clearPreview() {
    previewSeq.current += 1;
    previewAbortRef.current?.abort();
    previewAbortRef.current = null;
    setPreview(null);
    setPreviewing(false);
    if (!templateFileRequirements.length) {
      setTemplateNotice("");
    }
  }

  function clearTemplateRequirement(key: string) {
    setConfirmTemplateFileSkip(false);
    setTemplateFileRequirements((requirements) => {
      const nextRequirements = requirements.filter((requirement) => requirement.key !== key);
      if (requirements.length && !nextRequirements.length) {
        setTemplateNotice("Template files reattached.");
      }
      return nextRequirements;
    });
  }

  function clearTemplateFileRequirements() {
    if (!confirmTemplateFileSkip) {
      setConfirmTemplateFileSkip(true);
      setTemplateNotice(TEMPLATE_FILE_FALLBACK_WARNING);
      return;
    }
    setTemplateFileRequirements([]);
    setConfirmTemplateFileSkip(false);
    setTemplateNotice("Template file requirements cleared.");
  }

  function templateFileBlockMessage(): string {
    return `Reattach or clear required template files: ${templateFileRequirements
      .map((requirement) => requirement.label)
      .join("; ")}.`;
  }

  function fields(previewDigest?: string) {
    return {
      min_gene_set_size: minSize,
      max_gene_set_size: maxSize,
      go_namespace: goNamespace,
      preview_digest: previewDigest
    };
  }

  function files() {
    return {
      ranked_file: rankedFile,
      gene_set_file: geneSetFile,
      gene_set_obo_file: geneSetOboFile,
      gene_set_annotation_file: geneSetAnnotationFile
    };
  }

  function templateFields(): GseaTemplateFields {
    return {
      ranked,
      rankedFileName: rankedFile?.name ?? null,
      minSize,
      maxSize,
      goNamespace,
      geneSetCollection: collectionIdentity(geneSetFile, geneSetOboFile, geneSetAnnotationFile)
    };
  }

  function describeTemplate(template: RunTemplate<GseaTemplateFields>): string[] {
    return [
      `${template.fields.minSize}-${template.fields.maxSize} genes per set`,
      GO_NAMESPACE_OPTIONS.find((option) => option.value === template.fields.goNamespace)?.label ??
        template.fields.goNamespace,
      template.fields.rankedFileName
        ? `Input: file ${template.fields.rankedFileName}`
        : template.fields.rankedTextOmitted
          ? "Input: pasted ranked list not stored"
        : `Input: ${template.fields.ranked.split(/\n+/).filter((line) => line.trim()).length} pasted rows`,
      describeCollection(template.fields.geneSetCollection)
    ];
  }

  function onApplyTemplate(template: RunTemplate<GseaTemplateFields>) {
    const fields = template.fields;
    clearPreview();
    setError("");
    const requirements = templateRequirements(fields);
    setTemplateFileRequirements(requirements);
    setConfirmTemplateFileSkip(false);
    setFileInputResetKey((value) => value + 1);
    setTemplateNotice(fileNotice(fields));
    setRanked(fields.rankedTextOmitted ? "" : fields.ranked);
    setRankedFile(null);
    setGeneSetFile(null);
    setGeneSetOboFile(null);
    setGeneSetAnnotationFile(null);
    setMinSize(fields.minSize);
    setMaxSize(fields.maxSize);
    setGoNamespace(fields.goNamespace);
  }

  async function onPreview() {
    if (templateFileRequirements.length) {
      setError(templateFileBlockMessage());
      return;
    }
    const seq = previewSeq.current + 1;
    previewSeq.current = seq;
    previewAbortRef.current?.abort();
    const controller = new AbortController();
    previewAbortRef.current = controller;
    setPreviewing(true);
    setError("");
    try {
      const data = await previewTextJob(
        "/preview/gsea",
        "ranked_text",
        ranked,
        fields(),
        files(),
        { signal: controller.signal }
      );
      if (seq !== previewSeq.current || controller.signal.aborted) return;
      setPreview(data);
    } catch (err) {
      if (seq !== previewSeq.current || controller.signal.aborted) return;
      setPreview(null);
      setError(err instanceof Error ? err.message : "Failed to preview job");
    } finally {
      if (seq === previewSeq.current) {
        previewAbortRef.current = null;
        setPreviewing(false);
      }
    }
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (templateFileRequirements.length) {
      setError(templateFileBlockMessage());
      return;
    }
    if (!preview?.can_submit) {
      setError("Run preflight preview before queueing this job.");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const job = await submitTextJob(
        "/jobs/gsea",
        "ranked_text",
        ranked,
        fields(preview.preview_digest),
        files()
      );
      const tokenQuery = job.access_token ? `?token=${encodeURIComponent(job.access_token)}` : "";
      router.push(`/jobs/${job.id}${tokenQuery}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to submit job");
      setSubmitting(false);
    }
  }

  return (
    <>
      <section className="page-title">
        <div>
          <p className="eyebrow">Ranked enrichment</p>
          <h2>GSEA</h2>
          <p>Score ranked genes against the configured ANDES gene-set collection.</p>
        </div>
        <Link className="button secondary" href="/set-similarity">
          Switch to set similarity
        </Link>
      </section>

      <div className="workbench">
        <section className="panel form-panel">
          <form className="form" onSubmit={onSubmit}>
            <div className="field">
              <label htmlFor="ranked">Ranked genes</label>
              <textarea
                id="ranked"
                spellCheck={false}
                value={ranked}
                onChange={(event) => {
                  clearPreview();
                  setRanked(event.target.value);
                }}
              />
              <small>
                Example rows are from GSE3467_rank.txt in the benchmark suite. Use tab, comma, or
                whitespace between gene ID and score.
              </small>
            </div>

            <div className="file-grid">
              <div className="field">
                <label htmlFor="ranked-file">Ranked gene file</label>
                <input
                  key={`ranked-file-${fileInputResetKey}`}
                  id="ranked-file"
                  accept=".txt,.tsv,.csv"
                  type="file"
                  onChange={(event) => {
                    clearPreview();
                    const file = event.target.files?.[0] ?? null;
                    if (file) clearTemplateRequirement("ranked_file");
                    setRankedFile(file);
                  }}
                />
                <small>Optional. Gene ID and score per row; overrides the text box.</small>
              </div>
            </div>

            <GeneSetUploadPicker
              key={`gene-set-${fileInputResetKey}`}
              idPrefix="gene-set"
              title="Gene-set collection"
              description="Optional. Leave blank to use the configured benchmark GMT, or upload GMT or GO OBO plus annotations."
              gmtLabel="Gene-set GMT"
              oboLabel="GO OBO"
              annotationLabel="GO annotations"
              collectionLabel="gene-set"
              files={{
                gmtFile: geneSetFile,
                oboFile: geneSetOboFile,
                annotationFile: geneSetAnnotationFile
              }}
              onChange={(nextFiles) => {
                clearPreview();
                if (nextFiles.gmtFile) clearTemplateRequirement("gene_set_gmt");
                if (nextFiles.oboFile) clearTemplateRequirement("gene_set_obo");
                if (nextFiles.annotationFile) clearTemplateRequirement("gene_set_annotation");
                setGeneSetFile(nextFiles.gmtFile);
                setGeneSetOboFile(nextFiles.oboFile);
                setGeneSetAnnotationFile(nextFiles.annotationFile);
              }}
            />

            <div className="controls-grid">
              <div className="field">
                <label htmlFor="min-size">Min set size</label>
                <input
                  id="min-size"
                  min={1}
                  type="number"
                  value={minSize}
                  onChange={(event) => {
                    clearPreview();
                    setMinSize(Number(event.target.value));
                  }}
                />
              </div>
              <div className="field">
                <label htmlFor="max-size">Max set size</label>
                <input
                  id="max-size"
                  min={1}
                  type="number"
                  value={maxSize}
                  onChange={(event) => {
                    clearPreview();
                    setMaxSize(Number(event.target.value));
                  }}
                />
              </div>
              <div className="field">
                <label htmlFor="go-namespace">GO namespace</label>
                <select
                  id="go-namespace"
                  value={goNamespace}
                  onChange={(event) => {
                    clearPreview();
                    setGoNamespace(event.target.value as GoNamespace);
                  }}
                >
                  {GO_NAMESPACE_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {error ? <p className="error">{error}</p> : null}
            {templateNotice ? (
              <div className="template-warning">
                <p className="warning">{templateNotice}</p>
                {templateFileRequirements.length ? (
                  <>
                    <ul>
                      {templateFileRequirements.map((requirement) => (
                        <li key={requirement.key}>{requirement.label}</li>
                      ))}
                    </ul>
                    <button
                      className="button secondary compact"
                      type="button"
                      onClick={clearTemplateFileRequirements}
                    >
                      {confirmTemplateFileSkip
                        ? "Confirm use without files"
                        : "Continue without reattaching files"}
                    </button>
                  </>
                ) : null}
              </div>
            ) : null}

            <div className="actions">
              <span className="subtle">
                {rankedFile ? `Using ${rankedFile.name}` : `${rankedCount} ranked genes`}
              </span>
              <div className="button-row">
                <button
                  className="button secondary"
                  type="button"
                  disabled={previewing || submitting}
                  onClick={onPreview}
                >
                  {previewing ? "Checking..." : "Preview job"}
                </button>
                <button
                  className="button primary"
                  type="submit"
                  disabled={submitting || !preview?.can_submit}
                >
                  {submitting ? "Submitting..." : "Queue analysis"}
                </button>
              </div>
            </div>

            {preview ? <PreflightPreview preview={preview} /> : null}
          </form>
        </section>

        <aside className="side-panel">
          <RunTemplatePanel
            kind="gsea"
            currentFields={templateFields()}
            defaultName="GSEA template"
            describeTemplate={describeTemplate}
            mergeFieldsForUpdate={mergeTemplateFileIdentity}
            onApply={onApplyTemplate}
          />
          <section className="note">
            <h3>Scores</h3>
            <p>
              Higher scores should represent stronger association with the phenotype or contrast
              being tested.
            </p>
          </section>
          <section className="note">
            <h3>Output</h3>
            <p>
              Results show Z-score, raw p-value, FDR correction, and the significance flag returned
              by the backend.
            </p>
          </section>
          <section className="note">
            <h3>Runtime</h3>
            <p>
              The backend uses server defaults for workers and null sampling, which keeps expensive
              cache files shareable across matching jobs.
            </p>
          </section>
        </aside>
      </div>
    </>
  );
}
