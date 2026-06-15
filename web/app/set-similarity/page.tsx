"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useRef, useState } from "react";
import { GeneSetUploadPicker } from "@/components/gene-set-upload-picker";
import { PreflightPreview } from "@/components/preflight-preview";
import { RunTemplatePanel } from "@/components/run-template-panel";
import type { JobPreview } from "@/lib/api";
import { previewTextJob, submitTextJob } from "@/lib/api";
import { benchmarkSetSimilarityExample } from "@/lib/examples";
import type {
  GeneSetCollectionTemplate,
  GoNamespace,
  RunTemplate,
  SetSimilarityTemplateFields
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

function describeCollection(label: string, collection: GeneSetCollectionTemplate): string {
  if (collection.mode === "gmt") {
    return `${label}: GMT ${collection.gmtFileName ?? "file"}`;
  }
  if (collection.mode === "go") {
    return `${label}: GO ${collection.oboFileName ?? "OBO file"} + ${
      collection.annotationFileName ?? "annotation file"
    }`;
  }
  return `${label}: configured default`;
}

function fileNotice(fields: SetSimilarityTemplateFields): string {
  const files = [
    fields.genesFileName ? `gene list ${fields.genesFileName}` : "",
    fields.queryCollection.mode === "gmt" ? `query GMT ${fields.queryCollection.gmtFileName}` : "",
    fields.queryCollection.mode === "go"
      ? `query GO files ${fields.queryCollection.oboFileName ?? "OBO"} and ${
          fields.queryCollection.annotationFileName ?? "annotations"
        }`
      : "",
    fields.targetCollection.mode === "gmt" ? `target GMT ${fields.targetCollection.gmtFileName}` : "",
    fields.targetCollection.mode === "go"
      ? `target GO files ${fields.targetCollection.oboFileName ?? "OBO"} and ${
          fields.targetCollection.annotationFileName ?? "annotations"
        }`
      : ""
  ].filter(Boolean);
  const notices = [
    fields.genesTextOmitted ? TEMPLATE_TEXT_OMITTED_WARNING : "",
    files.length ? `${TEMPLATE_FILE_FALLBACK_WARNING} Reattach: ${files.join("; ")}.` : ""
  ].filter(Boolean);
  return notices.length ? notices.join(" ") : "Template applied.";
}

function collectionRequirements(
  prefix: string,
  label: string,
  collection: GeneSetCollectionTemplate
): TemplateFileRequirement[] {
  if (collection.mode === "gmt") {
    return collection.gmtFileName
      ? [{ key: `${prefix}_gmt`, label: `${label} GMT ${collection.gmtFileName}` }]
      : [];
  }
  if (collection.mode === "go") {
    return [
      collection.oboFileName
        ? { key: `${prefix}_obo`, label: `${label} OBO ${collection.oboFileName}` }
        : null,
      collection.annotationFileName
        ? {
            key: `${prefix}_annotation`,
            label: `${label} annotations ${collection.annotationFileName}`
          }
        : null
    ].filter((item): item is TemplateFileRequirement => item !== null);
  }
  return [];
}

function templateRequirements(fields: SetSimilarityTemplateFields): TemplateFileRequirement[] {
  return [
    fields.genesFileName
      ? { key: "genes_file", label: `gene list ${fields.genesFileName}` }
      : null,
    ...collectionRequirements("query", "query", fields.queryCollection),
    ...collectionRequirements("target", "target", fields.targetCollection)
  ].filter((item): item is TemplateFileRequirement => item !== null);
}

function mergeCollectionIdentity(
  current: GeneSetCollectionTemplate,
  existing: GeneSetCollectionTemplate
): GeneSetCollectionTemplate {
  return current.mode === "default" && existing.mode !== "default" ? existing : current;
}

function mergeTemplateFileIdentity(
  current: SetSimilarityTemplateFields,
  existing: SetSimilarityTemplateFields
): SetSimilarityTemplateFields {
  return {
    ...current,
    genesFileName: current.genesFileName ?? existing.genesFileName,
    queryCollection: mergeCollectionIdentity(current.queryCollection, existing.queryCollection),
    targetCollection: mergeCollectionIdentity(current.targetCollection, existing.targetCollection)
  };
}

export default function SetSimilarityPage() {
  const router = useRouter();
  const [genes, setGenes] = useState(benchmarkSetSimilarityExample);
  const [genesFile, setGenesFile] = useState<File | null>(null);
  const [queryGeneSetFile, setQueryGeneSetFile] = useState<File | null>(null);
  const [queryOboFile, setQueryOboFile] = useState<File | null>(null);
  const [queryAnnotationFile, setQueryAnnotationFile] = useState<File | null>(null);
  const [targetGeneSetFile, setTargetGeneSetFile] = useState<File | null>(null);
  const [targetOboFile, setTargetOboFile] = useState<File | null>(null);
  const [targetAnnotationFile, setTargetAnnotationFile] = useState<File | null>(null);
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
  const geneCount = genesFile ? null : genes.split(/\s+/).filter(Boolean).length;

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
      genes_file: genesFile,
      query_gene_set_file: queryGeneSetFile,
      query_obo_file: queryOboFile,
      query_annotation_file: queryAnnotationFile,
      gene_set_file: targetGeneSetFile,
      gene_set_obo_file: targetOboFile,
      gene_set_annotation_file: targetAnnotationFile
    };
  }

  function templateFields(): SetSimilarityTemplateFields {
    return {
      genes,
      genesFileName: genesFile?.name ?? null,
      minSize,
      maxSize,
      goNamespace,
      queryCollection: collectionIdentity(queryGeneSetFile, queryOboFile, queryAnnotationFile),
      targetCollection: collectionIdentity(targetGeneSetFile, targetOboFile, targetAnnotationFile)
    };
  }

  function describeTemplate(template: RunTemplate<SetSimilarityTemplateFields>): string[] {
    return [
      `${template.fields.minSize}-${template.fields.maxSize} genes per set`,
      GO_NAMESPACE_OPTIONS.find((option) => option.value === template.fields.goNamespace)?.label ??
        template.fields.goNamespace,
      template.fields.genesFileName
        ? `Input: file ${template.fields.genesFileName}`
        : template.fields.genesTextOmitted
          ? "Input: pasted genes not stored"
        : `Input: ${template.fields.genes.split(/\s+/).filter(Boolean).length} pasted genes`,
      describeCollection("Query", template.fields.queryCollection),
      describeCollection("Target", template.fields.targetCollection)
    ];
  }

  function onApplyTemplate(template: RunTemplate<SetSimilarityTemplateFields>) {
    const fields = template.fields;
    clearPreview();
    setError("");
    const requirements = templateRequirements(fields);
    setTemplateFileRequirements(requirements);
    setConfirmTemplateFileSkip(false);
    setFileInputResetKey((value) => value + 1);
    setTemplateNotice(fileNotice(fields));
    setGenes(fields.genesTextOmitted ? "" : fields.genes);
    setGenesFile(null);
    setQueryGeneSetFile(null);
    setQueryOboFile(null);
    setQueryAnnotationFile(null);
    setTargetGeneSetFile(null);
    setTargetOboFile(null);
    setTargetAnnotationFile(null);
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
        "/preview/set-similarity",
        "genes_text",
        genes,
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
        "/jobs/set-similarity",
        "genes_text",
        genes,
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
          <p className="eyebrow">BMA search</p>
          <h2>Set similarity</h2>
          <p>Compare a gene list against the configured gene-set database.</p>
        </div>
        <Link className="button secondary" href="/gsea">
          Switch to GSEA
        </Link>
      </section>

      <div className="workbench">
        <section className="panel form-panel">
          <form className="form" onSubmit={onSubmit}>
            <div className="field">
              <label htmlFor="genes">Input genes</label>
              <textarea
                id="genes"
                spellCheck={false}
                value={genes}
                onChange={(event) => {
                  clearPreview();
                  setGenes(event.target.value);
                }}
              />
              <small>
                Example genes come from GO:0043648 in the benchmark GMT. Use Entrez gene IDs for
                the current embedding.
              </small>
            </div>

            <GeneSetUploadPicker
              key={`query-${fileInputResetKey}`}
              idPrefix="query"
              title="Query collection"
              description="Optional. Upload either a GMT or GO OBO plus annotations to compare term sets instead of a single gene list."
              gmtLabel="Query GMT"
              oboLabel="Query GO OBO"
              annotationLabel="Query annotations"
              collectionLabel="query"
              files={{
                gmtFile: queryGeneSetFile,
                oboFile: queryOboFile,
                annotationFile: queryAnnotationFile
              }}
              onChange={(nextFiles) => {
                clearPreview();
                if (nextFiles.gmtFile) clearTemplateRequirement("query_gmt");
                if (nextFiles.oboFile) clearTemplateRequirement("query_obo");
                if (nextFiles.annotationFile) clearTemplateRequirement("query_annotation");
                setQueryGeneSetFile(nextFiles.gmtFile);
                setQueryOboFile(nextFiles.oboFile);
                setQueryAnnotationFile(nextFiles.annotationFile);
              }}
            />

            <GeneSetUploadPicker
              key={`target-${fileInputResetKey}`}
              idPrefix="target"
              title="Target collection"
              description="Optional. Leave blank to use the configured benchmark GMT, or upload GMT or GO OBO plus annotations."
              gmtLabel="Target GMT"
              oboLabel="Target GO OBO"
              annotationLabel="Target annotations"
              collectionLabel="target"
              files={{
                gmtFile: targetGeneSetFile,
                oboFile: targetOboFile,
                annotationFile: targetAnnotationFile
              }}
              onChange={(nextFiles) => {
                clearPreview();
                if (nextFiles.gmtFile) clearTemplateRequirement("target_gmt");
                if (nextFiles.oboFile) clearTemplateRequirement("target_obo");
                if (nextFiles.annotationFile) clearTemplateRequirement("target_annotation");
                setTargetGeneSetFile(nextFiles.gmtFile);
                setTargetOboFile(nextFiles.oboFile);
                setTargetAnnotationFile(nextFiles.annotationFile);
              }}
            />

            <div className="file-grid">
              <div className="field">
                <label htmlFor="genes-file">Gene list file</label>
                <input
                  key={`genes-file-${fileInputResetKey}`}
                  id="genes-file"
                  accept=".txt,.tsv,.csv"
                  type="file"
                  onChange={(event) => {
                    clearPreview();
                    const file = event.target.files?.[0] ?? null;
                    if (file) clearTemplateRequirement("genes_file");
                    setGenesFile(file);
                  }}
                />
                <small>Optional. One gene ID per line; overrides the text box when provided.</small>
              </div>
            </div>

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
                {genesFile ? `Using ${genesFile.name}` : `${geneCount} input genes`}
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
            kind="set_similarity"
            currentFields={templateFields()}
            defaultName="Set similarity template"
            describeTemplate={describeTemplate}
            mergeFieldsForUpdate={mergeTemplateFileIdentity}
            onApply={onApplyTemplate}
          />
          <section className="note">
            <h3>Input match</h3>
            <p>
              Gene IDs in gene lists, GMT files, or GO annotation files must match the embedding
              gene list. OBO uploads require a matching annotation file.
            </p>
          </section>
          <section className="note">
            <h3>Runtime</h3>
            <p>
              The server owns worker count and null iterations so cache files stay reusable across
              jobs. Local defaults are eight workers and 1,000 null samples.
            </p>
          </section>
        </aside>
      </div>
    </>
  );
}
