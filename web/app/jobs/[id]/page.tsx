"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import type { JobResponse, ResultTerm } from "@/lib/api";
import { cancelJob, getDownloadUrl, getJob } from "@/lib/api";

function formatDate(value?: string | null): string {
  if (!value) return "Not started";
  return new Intl.DateTimeFormat("en", {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(new Date(value));
}

function formatScore(value: number): string {
  if (!Number.isFinite(value)) return "NA";
  return value.toFixed(3);
}

function formatPValue(value: number): string {
  if (!Number.isFinite(value)) return "NA";
  if (value === 0) return "0";
  return value.toExponential(3);
}

function formatInteger(value: number): string {
  return new Intl.NumberFormat("en").format(value);
}

function formatSeconds(value?: number | null): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "NA";
  if (value < 0.001) return "<0.001 s";
  return `${value.toFixed(value >= 10 ? 1 : 3)} s`;
}

function exportSvg(svgId: string, filename: string) {
  const svg = document.getElementById(svgId);
  if (!(svg instanceof SVGElement)) return;
  const source = new XMLSerializer().serializeToString(svg);
  const blob = new Blob([source], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function ResultTable({ rows }: { rows: ResultTerm[] }) {
  const hasPairs = rows.some((row) => row.query_term || row.target_term);
  return (
    <table>
      <thead>
        {hasPairs ? (
          <tr>
            <th>Query term</th>
            <th>Target term</th>
            <th>Z-score</th>
            <th>FDR</th>
            <th>Significant</th>
          </tr>
        ) : (
          <tr>
            <th>Term</th>
            <th>Description</th>
            <th>Z-score</th>
            <th>FDR</th>
            <th>Significant</th>
          </tr>
        )}
      </thead>
      <tbody>
        {rows.slice(0, 100).map((row) => (
          <tr key={row.term}>
            {hasPairs ? (
              <>
                <td className="term-cell">
                  <strong>{row.query_term ?? ""}</strong>
                  {row.query_size ? <span>{row.query_size} genes</span> : null}
                  {row.query_description ? <span>{row.query_description}</span> : null}
                </td>
                <td className="term-cell">
                  <strong>{row.target_term ?? ""}</strong>
                  {row.target_size ? <span>{row.target_size} genes</span> : null}
                  {row.target_description ? <span>{row.target_description}</span> : null}
                </td>
              </>
            ) : (
              <>
                <td className="term-cell">
                  <strong>{row.term}</strong>
                  {row.size ? <span>{row.size} genes</span> : null}
                </td>
                <td>{row.description ?? ""}</td>
              </>
            )}
            <td className="mono">{formatScore(row.z_score)}</td>
            <td className="mono">{formatPValue(row.p_value_corrected)}</td>
            <td>{row.significant ? "yes" : "no"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

type MappingSummary = {
  mapped_count?: number;
  unmapped_count?: number;
  unmapped_examples?: string[];
  id_type_counts?: Record<string, number>;
};

type CacheProfile = {
  kind?: string;
  status?: string;
  hit?: boolean;
  path?: string;
  file?: string;
  seed?: number;
  seed_strategy?: string;
  requested_size_pairs?: number;
  added_size_pairs?: number;
  missing_size_pairs?: number;
  requested_sizes?: number;
  added_sizes?: number;
  missing_sizes?: number;
  cache_seconds?: number;
};

type TimingProfile = {
  cache?: number;
  scoring?: number;
  total?: number;
};

function readMapping(result: JobResponse["result"]): MappingSummary | null {
  const idMapping = result?.parameters.id_mapping;
  if (!idMapping || typeof idMapping !== "object") return null;
  const mapping = idMapping as Record<string, unknown>;
  const genes = mapping.genes;
  if (!genes || typeof genes !== "object") return null;
  return genes as MappingSummary;
}

function readCache(result: JobResponse["result"]): CacheProfile | null {
  const cache = result?.parameters.cache;
  if (!cache || typeof cache !== "object") return null;
  return cache as CacheProfile;
}

function readTiming(result: JobResponse["result"]): TimingProfile | null {
  const timing = result?.parameters.timing_seconds;
  if (!timing || typeof timing !== "object") return null;
  return timing as TimingProfile;
}

function CacheTransparency({
  cache,
  timing
}: {
  cache: CacheProfile;
  timing: TimingProfile | null;
}) {
  const requested = cache.requested_size_pairs ?? cache.requested_sizes ?? 0;
  const added = cache.added_size_pairs ?? cache.added_sizes ?? 0;
  const missing = cache.missing_size_pairs ?? cache.missing_sizes ?? added;
  return (
    <section className="panel pad">
      <div className="section-head">
        <div>
          <p className="eyebrow">Cache</p>
          <h2>Cache transparency</h2>
        </div>
        <span className={`status ${cache.hit ? "succeeded" : "running"}`}>
          {cache.hit ? "hit" : cache.status ?? "build"}
        </span>
      </div>
      <div className="preview-grid">
        <div className="preview-metric">
          <strong>{cache.status ?? "unknown"}</strong>
          <span>{cache.kind === "es" ? "ranked null cache" : "BMA null cache"}</span>
        </div>
        <div className="preview-metric">
          <strong>{formatInteger(added)}</strong>
          <span>{cache.kind === "es" ? "size buckets added" : "size pairs added"}</span>
          <small>{formatInteger(requested)} requested</small>
        </div>
        <div className="preview-metric">
          <strong>{formatInteger(missing)}</strong>
          <span>missing before run</span>
        </div>
        <div className="preview-metric">
          <strong>{cache.seed_strategy?.replaceAll("_", " ") ?? "unknown"}</strong>
          <span>seed strategy</span>
          {typeof cache.seed === "number" ? <small>seed {formatInteger(cache.seed)}</small> : null}
        </div>
        <div className="preview-metric">
          <strong>{formatSeconds(timing?.cache ?? cache.cache_seconds)}</strong>
          <span>cache phase</span>
        </div>
        <div className="preview-metric">
          <strong>{formatSeconds(timing?.scoring)}</strong>
          <span>scoring phase</span>
        </div>
        <div className="preview-metric">
          <strong>{formatSeconds(timing?.total)}</strong>
          <span>tracked total</span>
        </div>
      </div>
      <p className="cache-path">
        <strong>{cache.file ?? "cache file"}</strong>
        <span>{cache.path ?? ""}</span>
      </p>
    </section>
  );
}

function chartScale(value: number, min: number, max: number, start: number, end: number) {
  if (max === min) return (start + end) / 2;
  return start + ((value - min) / (max - min)) * (end - start);
}

function shortLabel(value: string, max = 24): string {
  if (value.length <= max) return value;
  return `${value.slice(0, Math.max(1, max - 1))}...`;
}

function DotPlot({ rows, jobId }: { rows: ResultTerm[]; jobId: string }) {
  const plotted = rows.slice(0, 120);
  if (plotted.length < 2) return null;
  const width = 760;
  const height = 280;
  const pad = 38;
  const values = plotted.map((row) => row.z_score);
  const minZ = Math.min(...values, 0);
  const maxZ = Math.max(...values, 0);
  const zeroY = chartScale(0, minZ, maxZ, height - pad, pad);
  const svgId = `z-dot-${jobId}`;
  return (
    <section className="panel pad chart-panel">
      <div className="section-head">
        <div>
          <p className="eyebrow">Visualization</p>
          <h2>Ranked Z-score plot</h2>
        </div>
        <button
          className="button secondary compact"
          type="button"
          onClick={() => exportSvg(svgId, `${jobId}-z-scores.svg`)}
        >
          Export SVG
        </button>
      </div>
      <svg
        aria-label="Ranked Z-score dot plot"
        className="svg-chart"
        id={svgId}
        role="img"
        viewBox={`0 0 ${width} ${height}`}
      >
        <rect width={width} height={height} fill="var(--surface)" />
        <line x1={pad} x2={width - pad} y1={zeroY} y2={zeroY} className="chart-axis" />
        <line x1={pad} x2={pad} y1={pad} y2={height - pad} className="chart-axis" />
        <line x1={pad} x2={width - pad} y1={height - pad} y2={height - pad} className="chart-axis" />
        <text x={pad} y={22} className="chart-label">
          z max {formatScore(maxZ)}
        </text>
        <text x={pad} y={height - 10} className="chart-label">
          top {plotted.length} ranked rows
        </text>
        {plotted.map((row, index) => {
          const x = chartScale(index, 0, plotted.length - 1, pad, width - pad);
          const y = chartScale(row.z_score, minZ, maxZ, height - pad, pad);
          return (
            <circle
              className={row.significant ? "chart-point significant" : "chart-point"}
              cx={x}
              cy={y}
              key={`${row.term}-${index}`}
              r={3.5}
            >
              <title>
                {row.term}: z={formatScore(row.z_score)}, FDR={formatPValue(row.p_value_corrected)}
              </title>
            </circle>
          );
        })}
      </svg>
    </section>
  );
}

function PairHeatmap({ rows, jobId }: { rows: ResultTerm[]; jobId: string }) {
  const pairs = rows.filter((row) => row.query_term && row.target_term).slice(0, 60);
  if (!pairs.length) return null;
  const queries = [...new Set(pairs.map((row) => row.query_term as string))].slice(0, 12);
  const targets = [...new Set(pairs.map((row) => row.target_term as string))].slice(0, 12);
  const width = 760;
  const left = 228;
  const top = 108;
  const available = width - left - 42;
  const cell = Math.max(30, Math.min(54, Math.floor(available / Math.max(1, targets.length))));
  const height = Math.max(260, top + queries.length * cell + 68);
  const maxAbs = Math.max(...pairs.map((row) => Math.abs(row.z_score)), 1);
  const svgId = `pair-heatmap-${jobId}`;
  const pairByKey = new Map(pairs.map((row) => [`${row.query_term}|${row.target_term}`, row]));
  return (
    <section className="panel pad chart-panel">
      <div className="section-head">
        <div>
          <p className="eyebrow">Visualization</p>
          <h2>Top-pair heatmap</h2>
        </div>
        <button
          className="button secondary compact"
          type="button"
          onClick={() => exportSvg(svgId, `${jobId}-pair-heatmap.svg`)}
        >
          Export SVG
        </button>
      </div>
      <svg
        aria-label="Top collection pair heatmap"
        className="svg-chart"
        id={svgId}
        role="img"
        viewBox={`0 0 ${width} ${height}`}
      >
        <rect width={width} height={height} fill="var(--surface)" />
        <text x={16} y={30} className="chart-title">
          Collection pair Z-scores
        </text>
        <text x={16} y={52} className="chart-label">
          Color intensity scales by absolute Z-score. Labels are truncated for readability.
        </text>
        <g transform={`translate(${width - 184} 28)`}>
          <rect className="heat-cell negative" height="14" opacity="0.75" width="28" x="0" y="0" />
          <text className="chart-label" x="36" y="12">
            negative
          </text>
          <rect className="heat-cell" height="14" opacity="0.75" width="28" x="104" y="0" />
          <text className="chart-label" x="140" y="12">
            positive
          </text>
        </g>
        {targets.map((target, index) => (
          <text
            className="chart-label heatmap-target"
            key={target}
            transform={`translate(${left + index * cell + cell / 2} ${top - 14}) rotate(-35)`}
          >
            <title>{target}</title>
            {shortLabel(target, 18)}
          </text>
        ))}
        {queries.map((query, queryIndex) => (
          <g key={query}>
            <text
              className="chart-label heatmap-query"
              x={left - 12}
              y={top + queryIndex * cell + cell / 2 + 4}
            >
              <title>{query}</title>
              {shortLabel(query, 26)}
            </text>
            {targets.map((target, targetIndex) => {
              const row = pairByKey.get(`${query}|${target}`);
              const intensity = row ? Math.min(Math.abs(row.z_score) / maxAbs, 1) : 0;
              const alpha = 0.12 + intensity * 0.78;
              return (
                <g key={target}>
                  <rect
                    className={row && row.z_score < 0 ? "heat-cell negative" : "heat-cell"}
                    height={cell - 4}
                    opacity={row ? alpha : 0.08}
                    width={cell - 4}
                    x={left + targetIndex * cell}
                    y={top + queryIndex * cell}
                  >
                    <title>
                      {row
                        ? `${query} vs ${target}: z=${formatScore(row.z_score)}`
                        : `${query} vs ${target}: not in top rows`}
                    </title>
                  </rect>
                  {row && cell >= 42 ? (
                    <text
                      className="heat-cell-label"
                      x={left + targetIndex * cell + cell / 2 - 2}
                      y={top + queryIndex * cell + cell / 2 + 4}
                    >
                      {formatScore(row.z_score)}
                    </text>
                  ) : null}
                </g>
              );
            })}
          </g>
        ))}
      </svg>
    </section>
  );
}

function PairNetwork({ rows, jobId }: { rows: ResultTerm[]; jobId: string }) {
  const pairs = rows.filter((row) => row.query_term && row.target_term).slice(0, 24);
  if (!pairs.length) return null;
  const queries = [...new Set(pairs.map((row) => row.query_term as string))].slice(0, 12);
  const targets = [...new Set(pairs.map((row) => row.target_term as string))].slice(0, 12);
  const width = 760;
  const height = Math.max(260, Math.max(queries.length, targets.length) * 34 + 92);
  const maxAbs = Math.max(...pairs.map((row) => Math.abs(row.z_score)), 1);
  const qY = new Map(queries.map((term, index) => [term, 76 + index * 34]));
  const tY = new Map(targets.map((term, index) => [term, 76 + index * 34]));
  const svgId = `pair-network-${jobId}`;
  return (
    <section className="panel pad chart-panel">
      <div className="section-head">
        <div>
          <p className="eyebrow">Visualization</p>
          <h2>Term-pair graph</h2>
        </div>
        <button
          className="button secondary compact"
          type="button"
          onClick={() => exportSvg(svgId, `${jobId}-pair-network.svg`)}
        >
          Export SVG
        </button>
      </div>
      <svg
        aria-label="Term pair network"
        className="svg-chart"
        id={svgId}
        role="img"
        viewBox={`0 0 ${width} ${height}`}
      >
        <rect width={width} height={height} fill="var(--surface)" />
        <text x={16} y={30} className="chart-title">
          Top term-pair links
        </text>
        <text x={16} y={52} className="chart-label">
          Thicker links have larger absolute Z-scores.
        </text>
        <text className="chart-label network-heading" x={220} y={58}>
          Query terms
        </text>
        <text className="chart-label network-heading" x={540} y={58}>
          Target terms
        </text>
        {pairs.map((row, index) => {
          const y1 = qY.get(row.query_term as string);
          const y2 = tY.get(row.target_term as string);
          if (y1 === undefined || y2 === undefined) return null;
          return (
            <g key={`${row.term}-${index}`}>
              <line
                className={row.z_score < 0 ? "network-edge negative" : "network-edge"}
                strokeWidth={1.5 + (Math.abs(row.z_score) / maxAbs) * 5}
                x1={238}
                x2={522}
                y1={y1}
                y2={y2}
              >
                <title>
                  {row.query_term} vs {row.target_term}: z={formatScore(row.z_score)}
                </title>
              </line>
              {pairs.length <= 8 ? (
                <text className="chart-label network-score" x={380} y={(y1 + y2) / 2 - 3}>
                  {formatScore(row.z_score)}
                </text>
              ) : null}
            </g>
          );
        })}
        {queries.map((term) => (
          <g key={term}>
            <circle className="network-node query" cx={238} cy={qY.get(term) ?? 0} r={6} />
            <text className="chart-label network-left" x={226} y={(qY.get(term) ?? 0) + 4}>
              <title>{term}</title>
              {shortLabel(term, 24)}
            </text>
          </g>
        ))}
        {targets.map((term) => (
          <g key={term}>
            <circle className="network-node target" cx={522} cy={tY.get(term) ?? 0} r={6} />
            <text className="chart-label network-right" x={534} y={(tY.get(term) ?? 0) + 4}>
              <title>{term}</title>
              {shortLabel(term, 24)}
            </text>
          </g>
        ))}
      </svg>
    </section>
  );
}

function DownloadLinks({
  jobId,
  collectionMode
}: {
  jobId: string;
  collectionMode: boolean;
}) {
  const links = [
    ["results.json", "JSON"],
    ["results.csv", "Results CSV"],
    ...(collectionMode
      ? [
          ["pair-table.csv", "Full pair table"],
          ["matrix.csv", "Z-score matrix"]
        ]
      : [])
  ];
  return (
    <section className="panel pad">
      <div className="section-head">
        <div>
          <p className="eyebrow">Downloads</p>
          <h2>Export results</h2>
        </div>
      </div>
      <div className="download-row">
        {links.map(([filename, label]) => (
          <a
            className="button secondary"
            href={getDownloadUrl(jobId, filename)}
            key={filename}
          >
            {label}
          </a>
        ))}
      </div>
    </section>
  );
}

export default function JobPage() {
  const params = useParams<{ id: string }>();
  const [jobResponse, setJobResponse] = useState<JobResponse | null>(null);
  const [error, setError] = useState("");
  const [cancelling, setCancelling] = useState(false);

  useEffect(() => {
    let active = true;
    async function poll() {
      try {
        const data = await getJob(params.id);
        if (!active) return;
        setJobResponse(data);
        if (data.job.state === "queued" || data.job.state === "running") {
          window.setTimeout(poll, 1500);
        }
      } catch (err) {
        if (active) setError(err instanceof Error ? err.message : "Failed to fetch job");
      }
    }
    poll();
    return () => {
      active = false;
    };
  }, [params.id]);

  if (error) {
    return <section className="panel pad error">{error}</section>;
  }
  if (!jobResponse) {
    return <section className="panel pad">Loading job...</section>;
  }

  const { job, result } = jobResponse;
  const collectionMode = result?.parameters.mode === "gene_set_collection";
  const geneMapping = readMapping(result);
  const cache = readCache(result);
  const timing = readTiming(result);
  const canCancel = job.state === "queued" || job.state === "running";

  async function onCancel() {
    setCancelling(true);
    setError("");
    try {
      setJobResponse(await cancelJob(job.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to cancel job");
    } finally {
      setCancelling(false);
    }
  }

  return (
    <>
      <section className="page-title">
        <div>
          <p className="eyebrow">{job.kind.replace("_", " ")}</p>
          <h2>Job results</h2>
          <p className="job-id">{job.id}</p>
        </div>
        <div className="page-actions">
          {canCancel ? (
            <button
              className="button secondary danger"
              disabled={cancelling}
              type="button"
              onClick={onCancel}
            >
              {cancelling ? "Cancelling..." : "Cancel job"}
            </button>
          ) : null}
          <span className={`status ${job.state}`}>{job.state}</span>
        </div>
      </section>

      <div className="job-layout">
        <aside className="panel job-meta">
          <h2>Run details</h2>
          <dl className="meta-list">
            <div>
              <dt>Created</dt>
              <dd>{formatDate(job.created_at)}</dd>
            </div>
            <div>
              <dt>Started</dt>
              <dd>{formatDate(job.started_at)}</dd>
            </div>
            <div>
              <dt>Finished</dt>
              <dd>{formatDate(job.finished_at)}</dd>
            </div>
            {job.cancelled_at ? (
              <div>
                <dt>Cancelled</dt>
                <dd>{formatDate(job.cancelled_at)}</dd>
              </div>
            ) : null}
            {jobResponse.queue?.position !== undefined && jobResponse.queue.position !== null ? (
              <div>
                <dt>Queue position</dt>
                <dd>
                  {jobResponse.queue.position === 0
                    ? "Running now"
                    : `#${jobResponse.queue.position}`}
                </dd>
              </div>
            ) : null}
          </dl>
          {job.error ? <p className="error">{job.error}</p> : null}
          {result?.warnings.length ? <p className="warning">{result.warnings.join(" ")}</p> : null}
        </aside>

        <div className="results-stack">
          {result ? (
            <>
              <div className="summary-grid" aria-label="Result summary">
                <div className="summary-card">
                  <strong>{result.valid_gene_count}</strong>
                  <span>{collectionMode ? "query terms" : "valid genes"}</span>
                </div>
                <div className="summary-card">
                  <strong>{result.invalid_genes.length}</strong>
                  <span>excluded genes</span>
                </div>
                <div className="summary-card">
                  <strong>
                    {typeof result.parameters.total_pairs === "number"
                      ? result.parameters.total_pairs
                      : result.results.length}
                  </strong>
                  <span>{collectionMode ? "scored pairs" : "scored terms"}</span>
                </div>
              </div>

              {job.state === "succeeded" ? (
                <DownloadLinks jobId={job.id} collectionMode={collectionMode} />
              ) : null}

              {cache ? <CacheTransparency cache={cache} timing={timing} /> : null}

              {geneMapping ? (
                <section className="panel pad">
                  <div className="section-head">
                    <div>
                      <p className="eyebrow">Gene IDs</p>
                      <h2>Mapping summary</h2>
                    </div>
                  </div>
                  <div className="preview-grid">
                    <div className="preview-metric">
                      <strong>{geneMapping.mapped_count ?? 0}</strong>
                      <span>mapped submitted IDs</span>
                    </div>
                    <div className="preview-metric">
                      <strong>{geneMapping.unmapped_count ?? 0}</strong>
                      <span>unmapped submitted IDs</span>
                    </div>
                  </div>
                  {geneMapping.id_type_counts ? (
                    <div className="chip-list compact">
                      {Object.entries(geneMapping.id_type_counts).map(([idType, count]) => (
                        <span key={idType}>
                          {idType}: {count}
                        </span>
                      ))}
                    </div>
                  ) : null}
                  {geneMapping.unmapped_examples?.length ? (
                    <p className="subtle">
                      Unmapped examples: {geneMapping.unmapped_examples.join(", ")}
                    </p>
                  ) : null}
                </section>
              ) : null}

              {collectionMode ? (
                <>
                  <PairHeatmap jobId={job.id} rows={result.results} />
                  <PairNetwork jobId={job.id} rows={result.results} />
                </>
              ) : (
                <DotPlot jobId={job.id} rows={result.results} />
              )}

              <section className="panel pad">
                <div className="section-head">
                  <div>
                    <p className="eyebrow">Top terms</p>
                    <h2>Results</h2>
                  </div>
                  <span className="subtle">Showing first 100 rows</span>
                </div>
                <div className="table-wrap">
                  <ResultTable rows={result.results} />
                </div>
              </section>
            </>
          ) : (
            <section className="panel pad">
              <p className="eyebrow">Queue</p>
              <h2>
                {job.state === "running"
                  ? "Running now"
                  : job.state === "queued"
                    ? "Waiting for results"
                    : "No results"}
              </h2>
              {job.state === "queued" && jobResponse.queue?.position ? (
                <p>
                  Queue position #{jobResponse.queue.position};{" "}
                  {jobResponse.queue.queued_ahead} job
                  {jobResponse.queue.queued_ahead === 1 ? "" : "s"} ahead.
                </p>
              ) : (
                <p>The page will refresh while the worker processes this job.</p>
              )}
            </section>
          )}
        </div>
      </div>
    </>
  );
}
