"use client";

import type { CollectionPreview, GenePreview, JobPreview } from "@/lib/api";

function formatInteger(value: number | undefined | null): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "NA";
  return new Intl.NumberFormat("en").format(value);
}

function cacheLabel(status: string): string {
  if (status === "reuse") return "Reuse cache";
  if (status === "build") return "Build cache";
  if (status === "extend_or_rebuild") return "Extend cache";
  return status;
}

function Metric({
  label,
  value,
  detail
}: {
  label: string;
  value: string;
  detail?: string;
}) {
  return (
    <div className="preview-metric">
      <strong>{value}</strong>
      <span>{label}</span>
      {detail ? <small>{detail}</small> : null}
    </div>
  );
}

function GeneSummary({ genes }: { genes: GenePreview }) {
  const idTypes = Object.entries(genes.id_type_counts);
  const sourceCounts = Object.entries(genes.source_counts ?? {});
  return (
    <div className="preview-block">
      <h4>Gene IDs</h4>
      <div className="mini-grid">
        <Metric label="matched genes" value={formatInteger(genes.matched_count)} />
        <Metric label="unmatched genes" value={formatInteger(genes.unmatched_count)} />
      </div>
      {idTypes.length ? (
        <div className="chip-list compact">
          {idTypes.map(([idType, count]) => (
            <span key={idType}>
              {idType}: {formatInteger(count)}
            </span>
          ))}
        </div>
      ) : null}
      {sourceCounts.length ? (
        <div className="chip-list compact">
          {sourceCounts.map(([source, count]) => (
            <span key={source}>
              {source}: {formatInteger(count)}
            </span>
          ))}
        </div>
      ) : null}
      {genes.unmatched_examples.length ? (
        <p className="subtle">
          Unmatched examples: {genes.unmatched_examples.slice(0, 8).join(", ")}
        </p>
      ) : null}
    </div>
  );
}

function CollectionSummary({
  title,
  collection
}: {
  title: string;
  collection: CollectionPreview;
}) {
  const sizeRange =
    collection.min_usable_size !== null && collection.max_usable_size !== null
      ? `${collection.min_usable_size}-${collection.max_usable_size} genes`
      : "no usable size range";
  return (
    <div className="preview-block">
      <h4>{title}</h4>
      <div className="mini-grid">
        <Metric
          label="usable terms"
          value={`${formatInteger(collection.usable_term_count)} / ${formatInteger(
            collection.term_count
          )}`}
        />
        <Metric
          label="matched genes"
          value={`${formatInteger(collection.matched_gene_count)} / ${formatInteger(
            collection.gene_count
          )}`}
          detail={sizeRange}
        />
      </div>
    </div>
  );
}

export function PreflightPreview({ preview }: { preview: JobPreview }) {
  const ready = preview.can_submit && !preview.over_limit;
  const missing =
    preview.cache.missing_size_pairs ?? preview.cache.missing_sizes ?? undefined;

  return (
    <section className={`preflight ${ready ? "ready" : "blocked"}`}>
      <div className="preflight-head">
        <div>
          <p className="eyebrow">Preflight</p>
          <h3>{ready ? "Ready to queue" : "Blocked by server limits"}</h3>
        </div>
        <span className={`status ${ready ? "succeeded" : "failed"}`}>
          {ready ? "valid" : "blocked"}
        </span>
      </div>

      <div className="preview-grid">
        <Metric
          label={preview.mode === "gene_set_collection" ? "estimated term pairs" : "scored terms"}
          value={formatInteger(preview.estimated_pair_count)}
          detail={`limit ${formatInteger(preview.max_term_pairs)}`}
        />
        <Metric
          label="cache plan"
          value={cacheLabel(preview.cache.status)}
          detail={[
            missing ? `${formatInteger(missing)} missing size bucket(s)` : null,
            preview.cache.seed_strategy
              ? `seed ${preview.cache.seed_strategy.replaceAll("_", " ")}`
              : null
          ]
            .filter(Boolean)
            .join("; ")}
        />
        <Metric
          label="analysis mode"
          value={preview.mode.replaceAll("_", " ")}
        />
      </div>

      {preview.genes ? <GeneSummary genes={preview.genes} /> : null}
      {preview.query_collection ? (
        <CollectionSummary title="Query collection" collection={preview.query_collection} />
      ) : null}
      {preview.target_collection ? (
        <CollectionSummary title="Target collection" collection={preview.target_collection} />
      ) : null}

      {preview.warnings.length ? (
        <ul className="preview-warnings">
          {preview.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
