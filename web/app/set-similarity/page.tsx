"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { PreflightPreview } from "@/components/preflight-preview";
import type { JobPreview } from "@/lib/api";
import { previewTextJob, submitTextJob } from "@/lib/api";
import { benchmarkSetSimilarityExample } from "@/lib/examples";

export default function SetSimilarityPage() {
  const router = useRouter();
  const [genes, setGenes] = useState(benchmarkSetSimilarityExample);
  const [genesFile, setGenesFile] = useState<File | null>(null);
  const [queryGeneSetFile, setQueryGeneSetFile] = useState<File | null>(null);
  const [queryOboFile, setQueryOboFile] = useState<File | null>(null);
  const [queryAnnotationFile, setQueryAnnotationFile] = useState<File | null>(null);
  const [queryGmtInputKey, setQueryGmtInputKey] = useState(0);
  const [queryGoInputKey, setQueryGoInputKey] = useState(0);
  const [targetGeneSetFile, setTargetGeneSetFile] = useState<File | null>(null);
  const [targetOboFile, setTargetOboFile] = useState<File | null>(null);
  const [targetAnnotationFile, setTargetAnnotationFile] = useState<File | null>(null);
  const [minSize, setMinSize] = useState(10);
  const [maxSize, setMaxSize] = useState(300);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<JobPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const geneCount = genesFile ? null : genes.split(/\s+/).filter(Boolean).length;
  const queryUsesGmt = queryGeneSetFile !== null;
  const queryUsesGo = queryOboFile !== null || queryAnnotationFile !== null;

  function clearPreview() {
    setPreview(null);
  }

  function fields() {
    return {
      min_gene_set_size: minSize,
      max_gene_set_size: maxSize
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

  function clearQueryUploads() {
    clearPreview();
    setQueryGeneSetFile(null);
    setQueryOboFile(null);
    setQueryAnnotationFile(null);
    setQueryGmtInputKey((value) => value + 1);
    setQueryGoInputKey((value) => value + 1);
  }

  function onQueryGmtChange(file: File | null) {
    clearPreview();
    setQueryGeneSetFile(file);
    if (file) {
      setQueryOboFile(null);
      setQueryAnnotationFile(null);
      setQueryGoInputKey((value) => value + 1);
    }
  }

  function onQueryGoChange(kind: "obo" | "annotation", file: File | null) {
    clearPreview();
    if (kind === "obo") {
      setQueryOboFile(file);
    } else {
      setQueryAnnotationFile(file);
    }
    if (file) {
      setQueryGeneSetFile(null);
      setQueryGmtInputKey((value) => value + 1);
    }
  }

  async function onPreview() {
    setPreviewing(true);
    setError("");
    try {
      const data = await previewTextJob(
        "/preview/set-similarity",
        "genes_text",
        genes,
        fields(),
        files()
      );
      setPreview(data);
    } catch (err) {
      setPreview(null);
      setError(err instanceof Error ? err.message : "Failed to preview job");
    } finally {
      setPreviewing(false);
    }
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
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
        fields(),
        files()
      );
      router.push(`/jobs/${job.id}`);
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

            <div className="upload-section">
              <div className="upload-section-head">
                <div>
                  <h3>Query collection</h3>
                  <p>
                    Optional. Upload either a GMT or GO OBO plus annotations to compare term sets
                    instead of a single gene list.
                  </p>
                </div>
                {queryUsesGmt || queryUsesGo ? (
                  <button
                    className="button secondary compact"
                    type="button"
                    onClick={clearQueryUploads}
                  >
                    Clear
                  </button>
                ) : null}
              </div>
              <div className="file-grid three">
                <div className="field">
                  <label htmlFor="query-gmt-file">Query GMT</label>
                  <input
                    key={`query-gmt-${queryGmtInputKey}`}
                    id="query-gmt-file"
                    accept=".gmt,.txt,.tsv"
                    disabled={queryUsesGo}
                    type="file"
                    onChange={(event) => onQueryGmtChange(event.target.files?.[0] ?? null)}
                  />
                  <small>
                    {queryUsesGo ? "Disabled while GO/OBO query files are selected." : ""}
                  </small>
                </div>
                <div className="field">
                  <label htmlFor="query-obo-file">Query GO OBO</label>
                  <input
                    key={`query-obo-${queryGoInputKey}`}
                    id="query-obo-file"
                    accept=".obo,.txt"
                    disabled={queryUsesGmt}
                    type="file"
                    onChange={(event) => onQueryGoChange("obo", event.target.files?.[0] ?? null)}
                  />
                  <small>{queryUsesGmt ? "Disabled while a query GMT is selected." : ""}</small>
                </div>
                <div className="field">
                  <label htmlFor="query-annotation-file">Query annotations</label>
                  <input
                    key={`query-annotation-${queryGoInputKey}`}
                    id="query-annotation-file"
                    accept=".gaf,.gpad,.txt,.tsv,.csv"
                    disabled={queryUsesGmt}
                    type="file"
                    onChange={(event) =>
                      onQueryGoChange("annotation", event.target.files?.[0] ?? null)
                    }
                  />
                  <small>{queryUsesGmt ? "Disabled while a query GMT is selected." : ""}</small>
                </div>
              </div>
            </div>

            <div className="upload-section">
              <div>
                <h3>Target collection</h3>
                <p>
                  Optional. Leave blank to use the configured benchmark GMT, or upload GMT or GO
                  OBO plus annotations.
                </p>
              </div>
              <div className="file-grid three">
                <div className="field">
                  <label htmlFor="target-gmt-file">Target GMT</label>
                  <input
                    id="target-gmt-file"
                    accept=".gmt,.txt,.tsv"
                    type="file"
                    onChange={(event) => {
                      clearPreview();
                      setTargetGeneSetFile(event.target.files?.[0] ?? null);
                    }}
                  />
                </div>
                <div className="field">
                  <label htmlFor="target-obo-file">Target GO OBO</label>
                  <input
                    id="target-obo-file"
                    accept=".obo,.txt"
                    type="file"
                    onChange={(event) => {
                      clearPreview();
                      setTargetOboFile(event.target.files?.[0] ?? null);
                    }}
                  />
                </div>
                <div className="field">
                  <label htmlFor="target-annotation-file">Target annotations</label>
                  <input
                    id="target-annotation-file"
                    accept=".gaf,.gpad,.txt,.tsv,.csv"
                    type="file"
                    onChange={(event) => {
                      clearPreview();
                      setTargetAnnotationFile(event.target.files?.[0] ?? null);
                    }}
                  />
                </div>
              </div>
            </div>

            <div className="file-grid">
              <div className="field">
                <label htmlFor="genes-file">Gene list file</label>
                <input
                  id="genes-file"
                  accept=".txt,.tsv,.csv"
                  type="file"
                  onChange={(event) => {
                    clearPreview();
                    setGenesFile(event.target.files?.[0] ?? null);
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
            </div>

            {error ? <p className="error">{error}</p> : null}

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
