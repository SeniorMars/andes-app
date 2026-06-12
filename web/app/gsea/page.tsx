"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { PreflightPreview } from "@/components/preflight-preview";
import type { JobPreview } from "@/lib/api";
import { previewTextJob, submitTextJob } from "@/lib/api";
import { benchmarkGseaExample } from "@/lib/examples";

export default function GseaPage() {
  const router = useRouter();
  const [ranked, setRanked] = useState(benchmarkGseaExample);
  const [rankedFile, setRankedFile] = useState<File | null>(null);
  const [geneSetFile, setGeneSetFile] = useState<File | null>(null);
  const [geneSetOboFile, setGeneSetOboFile] = useState<File | null>(null);
  const [geneSetAnnotationFile, setGeneSetAnnotationFile] = useState<File | null>(null);
  const [minSize, setMinSize] = useState(10);
  const [maxSize, setMaxSize] = useState(300);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<JobPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const rankedCount = rankedFile ? null : ranked.split(/\n+/).filter((line) => line.trim()).length;

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
      ranked_file: rankedFile,
      gene_set_file: geneSetFile,
      gene_set_obo_file: geneSetOboFile,
      gene_set_annotation_file: geneSetAnnotationFile
    };
  }

  async function onPreview() {
    setPreviewing(true);
    setError("");
    try {
      const data = await previewTextJob("/preview/gsea", "ranked_text", ranked, fields(), files());
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
      const job = await submitTextJob("/jobs/gsea", "ranked_text", ranked, fields(), files());
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
                  id="ranked-file"
                  accept=".txt,.tsv,.csv"
                  type="file"
                  onChange={(event) => {
                    clearPreview();
                    setRankedFile(event.target.files?.[0] ?? null);
                  }}
                />
                <small>Optional. Gene ID and score per row; overrides the text box.</small>
              </div>
            </div>

            <div className="upload-section">
              <div>
                <h3>Gene-set collection</h3>
                <p>
                  Optional. Leave blank to use the configured benchmark GMT, or upload GMT or GO
                  OBO plus annotations.
                </p>
              </div>
              <div className="file-grid three">
                <div className="field">
                  <label htmlFor="gene-set-file">Gene-set GMT</label>
                  <input
                    id="gene-set-file"
                    accept=".gmt,.txt,.tsv"
                    type="file"
                    onChange={(event) => {
                      clearPreview();
                      setGeneSetFile(event.target.files?.[0] ?? null);
                    }}
                  />
                </div>
                <div className="field">
                  <label htmlFor="gene-set-obo-file">GO OBO</label>
                  <input
                    id="gene-set-obo-file"
                    accept=".obo,.txt"
                    type="file"
                    onChange={(event) => {
                      clearPreview();
                      setGeneSetOboFile(event.target.files?.[0] ?? null);
                    }}
                  />
                </div>
                <div className="field">
                  <label htmlFor="gene-set-annotation-file">GO annotations</label>
                  <input
                    id="gene-set-annotation-file"
                    accept=".gaf,.gpad,.txt,.tsv,.csv"
                    type="file"
                    onChange={(event) => {
                      clearPreview();
                      setGeneSetAnnotationFile(event.target.files?.[0] ?? null);
                    }}
                  />
                </div>
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
