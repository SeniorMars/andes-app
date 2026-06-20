"use client";

import { useParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import type { JobResponse, ResultTerm } from "@/lib/api";
import {
  cancelJob,
  downloadJobArtifact,
  getJob,
  setStoredJobAccessToken
} from "@/lib/api";

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

const SVG_PRESENTATION_PROPERTIES = [
  "color",
  "fill",
  "fill-opacity",
  "font-family",
  "font-size",
  "font-weight",
  "opacity",
  "stroke",
  "stroke-dasharray",
  "stroke-linecap",
  "stroke-linejoin",
  "stroke-opacity",
  "stroke-width",
  "text-anchor"
];

function inlineSvgStyles(source: Element, clone: Element) {
  const computed = window.getComputedStyle(source);
  SVG_PRESENTATION_PROPERTIES.forEach((property) => {
    const value = computed.getPropertyValue(property);
    if (value) clone.setAttribute(property, value.trim());
  });
  Array.from(source.children).forEach((child, index) => {
    const clonedChild = clone.children.item(index);
    if (clonedChild) inlineSvgStyles(child, clonedChild);
  });
}

function exportSvg(svgId: string, filename: string) {
  const svg = document.getElementById(svgId);
  if (!(svg instanceof SVGElement)) return;
  const clone = svg.cloneNode(true) as SVGElement;
  clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  inlineSvgStyles(svg, clone);
  const source = new XMLSerializer().serializeToString(clone);
  downloadBlob(filename, new Blob([source], { type: "image/svg+xml;charset=utf-8" }));
}

function csvSafe(value: string | number | boolean | null | undefined): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  const text = String(value);
  if (/^[=+\-@]/.test(text.trimStart())) return `'${text}`;
  return text;
}

function csvCell(value: string | number | boolean | null | undefined): string {
  const text = csvSafe(value);
  if (!/[",\n\r]/.test(text)) return text;
  return `"${text.replaceAll('"', '""')}"`;
}

function downloadText(filename: string, text: string, type: string) {
  const blob = new Blob([text], { type });
  downloadBlob(filename, blob);
}

function downloadBlob(filename: string, blob: Blob) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function rowSearchText(row: ResultTerm): string {
  return [
    row.term,
    row.description,
    row.query_term,
    row.query_description,
    row.target_term,
    row.target_description
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

function rowSortLabel(row: ResultTerm): string {
  return row.query_term && row.target_term ? `${row.query_term} ${row.target_term}` : row.term;
}

function fdrValue(row: ResultTerm): number {
  return typeof row.p_value_corrected === "number" && Number.isFinite(row.p_value_corrected)
    ? row.p_value_corrected
    : 1;
}

function exportResultRows(rows: ResultTerm[]) {
  const header = [
    "term",
    "description",
    "size",
    "query_term",
    "query_description",
    "query_size",
    "target_term",
    "target_description",
    "target_size",
    "true_score",
    "z_score",
    "p_value",
    "p_value_corrected",
    "log10_p_value_corrected",
    "significant"
  ];
  const csv = [
    header.join(","),
    ...rows.map((row) =>
      [
        row.term,
        row.description,
        row.size,
        row.query_term,
        row.query_description,
        row.query_size,
        row.target_term,
        row.target_description,
        row.target_size,
        row.true_score,
        row.z_score,
        row.p_value,
        row.p_value_corrected,
        row.log10_p_value_corrected,
        row.significant
      ]
        .map(csvCell)
        .join(",")
    )
  ].join("\n");
  downloadText("andes-filtered-results.csv", `${csv}\n`, "text/csv;charset=utf-8");
}

function ResultTable({ rows }: { rows: ResultTerm[] }) {
  const [search, setSearch] = useState("");
  const [maxFdr, setMaxFdr] = useState(1);
  const [significantOnly, setSignificantOnly] = useState(false);
  const [sortKey, setSortKey] = useState("fdr-asc");
  const hasPairs = useMemo(() => rows.some((row) => row.query_term || row.target_term), [rows]);
  const filteredRows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return [...rows]
      .filter((row) => {
        const fdr = fdrValue(row);
        if (fdr > maxFdr) return false;
        if (significantOnly && !row.significant) return false;
        return !needle || rowSearchText(row).includes(needle);
      })
      .sort((a, b) => {
        if (sortKey === "z-desc") return b.z_score - a.z_score;
        if (sortKey === "z-asc") return a.z_score - b.z_score;
        if (sortKey === "term-asc") return rowSortLabel(a).localeCompare(rowSortLabel(b));
        return fdrValue(a) - fdrValue(b);
      });
  }, [maxFdr, rows, search, significantOnly, sortKey]);
  const visibleRows = filteredRows.slice(0, 500);
  return (
    <>
      <div className="result-controls">
        <label className="field">
          <span>Search terms</span>
          <input
            placeholder="Term, description, query, target"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
        <label className="field">
          <span>Max FDR: {maxFdr.toFixed(2)}</span>
          <input
            max={1}
            min={0}
            step={0.01}
            type="range"
            value={maxFdr}
            onChange={(event) => setMaxFdr(Number(event.target.value))}
          />
        </label>
        <label className="field">
          <span>Sort</span>
          <select value={sortKey} onChange={(event) => setSortKey(event.target.value)}>
            <option value="fdr-asc">FDR ascending</option>
            <option value="z-desc">Z-score descending</option>
            <option value="z-asc">Z-score ascending</option>
            <option value="term-asc">Term A-Z</option>
          </select>
        </label>
        <label className="check-row">
          <input
            checked={significantOnly}
            type="checkbox"
            onChange={(event) => setSignificantOnly(event.target.checked)}
          />
          <span>Significant only</span>
        </label>
        <button
          className="button secondary"
          disabled={!filteredRows.length}
          type="button"
          onClick={() => exportResultRows(filteredRows)}
        >
          Export filtered CSV
        </button>
      </div>
      <p className="subtle">
        Showing {visibleRows.length} of {filteredRows.length} filtered rows from {rows.length} total.
      </p>
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
          {visibleRows.map((row, index) => (
            <tr key={`${row.term}-${row.query_term ?? ""}-${row.target_term ?? ""}-${index}`}>
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
    </>
  );
}

type MappingSummary = {
  mapped_count?: number;
  submitted_record_count?: number;
  unresolved_count?: number;
  unmapped_count?: number;
  unmapped_examples?: string[];
  ambiguous_count?: number;
  ambiguous_examples?: string[];
  id_type_counts?: Record<string, number>;
  source_counts?: Record<string, number>;
  mapping_provenance?: MappingProvenance;
};

type MappingProvenance = {
  species?: string | null;
  mapping_file?: string | null;
  mapping_mtime_ns?: number | null;
  mapping_size?: number | null;
  mapping_sha256?: string | null;
  gene_list_file?: string | null;
  gene_list_mtime_ns?: number | null;
  gene_list_size?: number | null;
  sqlite_file?: string | null;
  alias_rows?: number | null;
};

type MappingEntry = {
  key: string;
  label: string;
  mapping: MappingSummary;
};

type GseaTracePoint = {
  rank: number;
  gene: string;
  rank_score: number;
  best_match_gene: string;
  match_score: number;
  centered_score: number;
  running_es: number;
};

type GseaTraceTerm = {
  term: string;
  description?: string | null;
  size?: number | null;
  true_score?: number | null;
  z_score?: number | null;
  p_value_corrected?: number | null;
  es?: number | null;
  es_rank?: number | null;
  sampled?: boolean;
  points: GseaTracePoint[];
};

type GseaTrace = {
  algorithm?: string;
  exact?: boolean;
  ranked_gene_count: number;
  max_points_per_term?: number;
  terms: GseaTraceTerm[];
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
  trace?: number;
  total?: number;
};

function mappingLabel(key: string): string {
  if (key === "genes") return "Input genes";
  if (key === "query_collection") return "Query collection";
  if (key === "target_collection") return "Target collection";
  return key.replaceAll("_", " ");
}

function readMappings(result: JobResponse["result"]): MappingEntry[] {
  const idMapping = result?.parameters.id_mapping;
  if (!idMapping || typeof idMapping !== "object") return [];
  return Object.entries(idMapping as Record<string, unknown>)
    .filter((entry): entry is [string, Record<string, unknown>] => {
      const [, payload] = entry;
      return Boolean(payload && typeof payload === "object");
    })
    .map(([key, payload]) => ({
      key,
      label: mappingLabel(key),
      mapping: payload as MappingSummary
    }));
}

function excludedGeneCount(
  result: JobResponse["result"],
  mappings: MappingEntry[]
): number {
  const genes = mappings.find((entry) => entry.key === "genes")?.mapping;
  if (genes) {
    if (typeof genes.unresolved_count === "number") return genes.unresolved_count;
    return (genes.unmapped_count ?? 0) + (genes.ambiguous_count ?? 0);
  }
  return result?.invalid_genes.length ?? 0;
}

function readGseaTrace(result: JobResponse["result"]): GseaTrace | null {
  const trace = result?.parameters.gsea_trace;
  if (!trace || typeof trace !== "object") return null;
  const payload = trace as { ranked_gene_count?: unknown; terms?: unknown };
  if (typeof payload.ranked_gene_count !== "number" || !Array.isArray(payload.terms)) {
    return null;
  }
  const terms = payload.terms.filter((term): term is GseaTraceTerm => {
    if (!term || typeof term !== "object") return false;
    const maybeTerm = term as { term?: unknown; points?: unknown };
    return typeof maybeTerm.term === "string" && Array.isArray(maybeTerm.points);
  });
  if (!terms.length) return null;
  return { ...(trace as GseaTrace), terms };
}

function hasMappingProvenance(result: JobResponse["result"]): boolean {
  const idMapping = result?.parameters.id_mapping;
  if (!idMapping || typeof idMapping !== "object") return false;
  return Object.values(idMapping).some((payload) => {
    if (!payload || typeof payload !== "object") return false;
    const provenance = (payload as { mapping_provenance?: unknown }).mapping_provenance;
    return Boolean(provenance && typeof provenance === "object");
  });
}

function hasMappingReport(result: JobResponse["result"]): boolean {
  const idMapping = result?.parameters.id_mapping;
  if (!idMapping || typeof idMapping !== "object") return false;
  return Object.values(idMapping).some((payload) => {
    if (!payload || typeof payload !== "object") return false;
    const mapping = payload as {
      mapping_report?: unknown;
      records?: unknown;
      submitted_record_count?: unknown;
    };
    if (mapping.mapping_report === "mapping-report.csv") return true;
    if (typeof mapping.submitted_record_count === "number" && mapping.submitted_record_count > 0) {
      return true;
    }
    return Array.isArray(mapping.records) && mapping.records.some((record) => {
      return record && typeof record === "object";
    });
  });
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

function GseaRunningScorePlot({ trace, jobId }: { trace: GseaTrace; jobId: string }) {
  const terms = trace.terms.filter((term) => term.points.length >= 2);
  const [selectedTerm, setSelectedTerm] = useState(terms[0]?.term ?? "");
  const selected = terms.find((term) => term.term === selectedTerm) ?? terms[0];
  if (!selected) return null;
  const points = [...selected.points].sort((a, b) => a.rank - b.rank);
  const width = 820;
  const height = 390;
  const left = 54;
  const right = 28;
  const top = 42;
  const esBottom = 246;
  const scoreTop = 286;
  const bottom = 346;
  const maxRank = Math.max(trace.ranked_gene_count, ...points.map((point) => point.rank));
  const runningValues = points.map((point) => point.running_es);
  const scoreValues = points.map((point) => point.match_score);
  const minEs = Math.min(...runningValues, 0);
  const maxEs = Math.max(...runningValues, 0);
  const minScore = Math.min(...scoreValues);
  const maxScore = Math.max(...scoreValues);
  const svgId = `gsea-running-${jobId}`;
  const xFor = (rank: number) => chartScale(rank, 1, maxRank, left, width - right);
  const esY = (value: number) => chartScale(value, minEs, maxEs, esBottom, top);
  const scoreY = (value: number) => chartScale(value, minScore, maxScore, bottom, scoreTop);
  const path = points
    .map((point) => `${xFor(point.rank).toFixed(2)},${esY(point.running_es).toFixed(2)}`)
    .join(" ");
  const peak = points.reduce((best, point) => {
    if (selected.es_rank && point.rank === selected.es_rank) return point;
    return Math.abs(point.running_es) > Math.abs(best.running_es) ? point : best;
  }, points[0]);
  const title = selected.description || selected.term;
  return (
    <section className="panel pad chart-panel">
      <div className="section-head">
        <div>
          <p className="eyebrow">GSEA trace</p>
          <h2>Running score</h2>
        </div>
        <button
          className="button secondary compact"
          type="button"
          onClick={() => exportSvg(svgId, `${jobId}-gsea-running-score.svg`)}
        >
          Export SVG
        </button>
      </div>
      {terms.length > 1 ? (
        <div className="segmented-row">
          {terms.map((term) => (
            <button
              className={term.term === selected.term ? "segment active" : "segment"}
              key={term.term}
              type="button"
              onClick={() => setSelectedTerm(term.term)}
            >
              {shortLabel(term.description || term.term, 28)}
            </button>
          ))}
        </div>
      ) : null}
      <svg
        aria-label="GSEA running score plot"
        className="svg-chart"
        id={svgId}
        role="img"
        viewBox={`0 0 ${width} ${height}`}
      >
        <rect width={width} height={height} fill="var(--surface)" />
        <line x1={left} x2={width - right} y1={esY(0)} y2={esY(0)} className="chart-axis soft" />
        <line x1={left} x2={left} y1={top} y2={bottom} className="chart-axis" />
        <line x1={left} x2={width - right} y1={esBottom} y2={esBottom} className="chart-axis" />
        <line x1={left} x2={width - right} y1={bottom} y2={bottom} className="chart-axis" />
        <text x={left} y={22} className="chart-title">
          {shortLabel(title, 72)}
        </text>
        <text x={left} y={height - 14} className="chart-label">
          {formatInteger(points.length)} sampled ranks of {formatInteger(trace.ranked_gene_count)};
          peak rank {selected.es_rank ?? peak.rank}
        </text>
        <text x={width - right} y={22} className="chart-label align-end">
          z {typeof selected.z_score === "number" ? formatScore(selected.z_score) : "NA"}; FDR{" "}
          {typeof selected.p_value_corrected === "number"
            ? formatPValue(selected.p_value_corrected)
            : "NA"}
        </text>
        <text x={left} y={esBottom + 20} className="chart-label">
          running ES
        </text>
        <text x={left} y={bottom + 22} className="chart-label">
          best-match score
        </text>
        {points.map((point) => {
          const x = xFor(point.rank);
          return (
            <line
              className="gsea-match-bar"
              key={`${point.rank}-${point.gene}`}
              x1={x}
              x2={x}
              y1={bottom}
              y2={scoreY(point.match_score)}
            >
              <title>
                rank {point.rank}: {point.gene} best matches {point.best_match_gene}; score{" "}
                {formatScore(point.match_score)}
              </title>
            </line>
          );
        })}
        <polyline className="gsea-running-line" points={path} />
        <line
          className="gsea-peak-line"
          x1={xFor(selected.es_rank ?? peak.rank)}
          x2={xFor(selected.es_rank ?? peak.rank)}
          y1={top}
          y2={bottom}
        />
        <circle className="chart-point significant" cx={xFor(peak.rank)} cy={esY(peak.running_es)} r={4.5}>
          <title>
            ES peak rank {selected.es_rank ?? peak.rank}: {formatScore(peak.running_es)}
          </title>
        </circle>
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
  collectionMode,
  hasMapping,
  hasMappingProvenance
}: {
  jobId: string;
  collectionMode: boolean;
  hasMapping: boolean;
  hasMappingProvenance: boolean;
}) {
  const [downloading, setDownloading] = useState("");
  const [downloadError, setDownloadError] = useState("");
  const links: Array<[string, string]> = [
    ["results.json", "JSON"],
    ["results.csv", "Results CSV"],
    ["report.zip", "Report ZIP"]
  ];
  if (hasMapping) links.splice(2, 0, ["mapping-report.csv", "Mapping report"]);
  if (hasMappingProvenance) {
    links.splice(3, 0, ["mapping-provenance.json", "Mapping provenance"]);
  }
  if (collectionMode) {
    links.push(["pair-table.csv", "Full pair table"], ["matrix.csv", "Z-score matrix"]);
  }
  async function handleDownload(filename: string) {
    setDownloading(filename);
    setDownloadError("");
    try {
      const artifact = await downloadJobArtifact(jobId, filename);
      downloadBlob(artifact.filename, artifact.blob);
    } catch (err) {
      setDownloadError(err instanceof Error ? err.message : "Download failed");
    } finally {
      setDownloading("");
    }
  }
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
          <button
            className="button secondary"
            disabled={downloading === filename}
            key={filename}
            onClick={() => void handleDownload(filename)}
            type="button"
          >
            {downloading === filename ? "Downloading..." : label}
          </button>
        ))}
      </div>
      {downloadError ? <p className="form-error">{downloadError}</p> : null}
    </section>
  );
}

export default function JobPage() {
  const params = useParams<{ id: string }>();
  const [jobResponse, setJobResponse] = useState<JobResponse | null>(null);
  const [error, setError] = useState("");
  const [cancelling, setCancelling] = useState(false);
  const urlTokenRef = useRef<{ jobId: string; token: string } | null>(null);

  useEffect(() => {
    let active = true;
    const urlToken = new URLSearchParams(window.location.search).get("token")?.trim();
    if (urlToken) {
      urlTokenRef.current = { jobId: params.id, token: urlToken };
      setStoredJobAccessToken(params.id, urlToken);
      const url = new URL(window.location.href);
      url.searchParams.delete("token");
      window.history.replaceState(
        null,
        "",
        `${url.pathname}${url.search}${url.hash}`
      );
    }
    const rememberedToken =
      urlTokenRef.current?.jobId === params.id ? urlTokenRef.current.token : undefined;
    const accessToken = urlToken || rememberedToken;
    async function poll() {
      try {
        const data = await getJob(params.id, accessToken || undefined);
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
  const mappingSummaries = readMappings(result);
  const excludedCount = excludedGeneCount(result, mappingSummaries);
  const gseaTrace = readGseaTrace(result);
  const mappingReportAvailable = hasMappingReport(result);
  const mappingProvenanceAvailable = hasMappingProvenance(result);
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
                  <strong>{excludedCount}</strong>
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
                <DownloadLinks
                  jobId={job.id}
                  collectionMode={collectionMode}
                  hasMapping={mappingReportAvailable}
                  hasMappingProvenance={mappingProvenanceAvailable}
                />
              ) : null}

              {cache ? <CacheTransparency cache={cache} timing={timing} /> : null}

              {mappingSummaries.length ? (
                <section className="panel pad">
                  <div className="section-head">
                    <div>
                      <p className="eyebrow">Gene IDs</p>
                      <h2>Mapping summary</h2>
                    </div>
                  </div>
                  {mappingSummaries.map(({ key, label, mapping }) => (
                    <div className="mapping-summary" key={key}>
                      {mappingSummaries.length > 1 ? <h3>{label}</h3> : null}
                      <div className="preview-grid">
                        <div className="preview-metric">
                          <strong>{mapping.mapped_count ?? 0}</strong>
                          <span>mapped submitted IDs</span>
                        </div>
                        <div className="preview-metric">
                          <strong>{mapping.unmapped_count ?? 0}</strong>
                          <span>unmapped submitted IDs</span>
                        </div>
                        <div className="preview-metric">
                          <strong>{mapping.ambiguous_count ?? 0}</strong>
                          <span>ambiguous submitted IDs</span>
                        </div>
                      </div>
                      {mapping.id_type_counts ? (
                        <div className="chip-list compact">
                          {Object.entries(mapping.id_type_counts).map(([idType, count]) => (
                            <span key={idType}>
                              {idType}: {count}
                            </span>
                          ))}
                        </div>
                      ) : null}
                      {mapping.source_counts ? (
                        <div className="chip-list compact">
                          {Object.entries(mapping.source_counts).map(([source, count]) => (
                            <span key={source}>
                              {source}: {count}
                            </span>
                          ))}
                        </div>
                      ) : null}
                      {mapping.ambiguous_examples?.length ? (
                        <p className="subtle">
                          Ambiguous examples: {mapping.ambiguous_examples.join(", ")}
                        </p>
                      ) : null}
                      {mapping.mapping_provenance ? (
                        <p className="subtle">
                          Mapping snapshot: {mapping.mapping_provenance.mapping_file ?? "unknown"}
                          {mapping.mapping_provenance.species
                            ? ` (${mapping.mapping_provenance.species})`
                            : ""}
                          {typeof mapping.mapping_provenance.mapping_size === "number"
                            ? `, ${formatInteger(mapping.mapping_provenance.mapping_size)} bytes`
                            : ""}
                          {mapping.mapping_provenance.mapping_sha256
                            ? `, sha256 ${mapping.mapping_provenance.mapping_sha256.slice(0, 12)}`
                            : ""}
                        </p>
                      ) : null}
                      {mapping.unmapped_examples?.length ? (
                        <p className="subtle">
                          Unmapped examples: {mapping.unmapped_examples.join(", ")}
                        </p>
                      ) : null}
                    </div>
                  ))}
                </section>
              ) : null}

              {collectionMode ? (
                <>
                  <PairHeatmap jobId={job.id} rows={result.results} />
                  <PairNetwork jobId={job.id} rows={result.results} />
                </>
              ) : (
                <>
                  {gseaTrace ? <GseaRunningScorePlot jobId={job.id} trace={gseaTrace} /> : null}
                  <DotPlot jobId={job.id} rows={result.results} />
                </>
              )}

              <section className="panel pad">
                <div className="section-head">
                  <div>
                    <p className="eyebrow">Top terms</p>
                    <h2>Results</h2>
                  </div>
                  <span className="subtle">Filter, sort, and export current rows</span>
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
