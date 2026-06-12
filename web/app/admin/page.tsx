"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { AdminPasswordPanel } from "@/components/admin-password-panel";
import type { DataStatus } from "@/lib/api";
import {
  clearStoredAdminToken,
  getDataStatus,
  isAdminAuthError,
  setStoredAdminToken
} from "@/lib/api";

function formatBytes(value: number): string {
  if (!Number.isFinite(value)) return "NA";
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let scaled = value / 1024;
  let unit = units[0];
  for (let index = 1; index < units.length && scaled >= 1024; index += 1) {
    scaled /= 1024;
    unit = units[index];
  }
  return `${scaled.toFixed(scaled >= 10 ? 1 : 2)} ${unit}`;
}

function formatTime(timestamp?: number | null): string {
  if (!timestamp) return "No files";
  return new Intl.DateTimeFormat("en", {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(new Date(timestamp * 1000));
}

export default function AdminPage() {
  const [status, setStatus] = useState<DataStatus | null>(null);
  const [error, setError] = useState("");
  const [needsPassword, setNeedsPassword] = useState(false);

  const load = useCallback(async () => {
    try {
      setStatus(await getDataStatus());
      setNeedsPassword(false);
      setError("");
    } catch (err) {
      if (isAdminAuthError(err)) {
        clearStoredAdminToken();
        setStatus(null);
        setNeedsPassword(true);
        setError("Enter the admin password to continue.");
        return;
      }
      setError(err instanceof Error ? err.message : "Failed to load status");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  function onUnlock(password: string) {
    setStoredAdminToken(password);
    setNeedsPassword(false);
    setError("");
    void load();
  }

  if (needsPassword) {
    return <AdminPasswordPanel error={error} onSubmit={onUnlock} />;
  }
  if (error) {
    return <section className="panel pad error">{error}</section>;
  }
  if (!status) {
    return <section className="panel pad">Loading status...</section>;
  }

  const cacheRows = [
    ["BMA", status.cache.bma],
    ["GSEA", status.cache.es]
  ] as const;

  return (
    <>
      <section className="page-title">
        <div>
          <p className="eyebrow">Admin</p>
          <h2>Server status</h2>
          <p>Operational view of configured data, caches, job storage, and limits.</p>
        </div>
        <div className="page-actions">
          <Link className="button secondary" href="/admin/queue">
            Queue
          </Link>
          <span className={`status ${status.ready ? "succeeded" : "failed"}`}>
            {status.ready ? "ready" : "blocked"}
          </span>
        </div>
      </section>

      <section className="grid" aria-label="Admin status">
        <article className="panel pad">
          <p className="eyebrow">Data paths</p>
          <h2>Readiness</h2>
          <dl className="meta-list">
            {Object.entries(status.checks).map(([name, ok]) => (
              <div key={name}>
                <dt>{name.replaceAll("_", " ")}</dt>
                <dd>{ok ? "present" : "missing"}</dd>
              </div>
            ))}
          </dl>
        </article>

        <article className="panel pad">
          <p className="eyebrow">Jobs</p>
          <h2>Run storage</h2>
          <div className="preview-grid">
            <div className="preview-metric">
              <strong>{status.jobs.run_directories}</strong>
              <span>run directories</span>
            </div>
            <div className="preview-metric">
              <strong>{formatBytes(status.jobs.run_bytes)}</strong>
              <span>run storage</span>
            </div>
          </div>
          <div className="chip-list compact">
            {Object.entries(status.jobs.job_counts).map(([state, count]) => (
              <span key={state}>
                {state}: {count}
              </span>
            ))}
          </div>
        </article>
      </section>

      <section className="panel pad">
        <div className="section-head">
          <div>
            <p className="eyebrow">Cache</p>
            <h2>Null-cache storage</h2>
          </div>
          <span className={`status ${status.cache.exists ? "succeeded" : "failed"}`}>
            {status.cache.exists ? "configured" : "missing"}
          </span>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Cache</th>
                <th>Files</th>
                <th>Bytes</th>
                <th>Newest use</th>
              </tr>
            </thead>
            <tbody>
              {cacheRows.map(([label, cache]) => (
                <tr key={label}>
                  <td>{label}</td>
                  <td>{cache.files}</td>
                  <td>{formatBytes(cache.bytes)}</td>
                  <td>{formatTime(cache.newest_mtime)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel pad">
        <div className="section-head">
          <div>
            <p className="eyebrow">Config</p>
            <h2>Server-owned limits</h2>
          </div>
        </div>
        <div className="config-grid">
          {Object.entries(status.config).map(([name, value]) => (
            <div className="preview-metric" key={name}>
              <strong>{String(value ?? "unset")}</strong>
              <span>{name.replaceAll("_", " ")}</span>
            </div>
          ))}
        </div>
      </section>
    </>
  );
}
