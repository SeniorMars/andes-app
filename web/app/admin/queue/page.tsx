"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { AdminPasswordPanel } from "@/components/admin-password-panel";
import type { AdminQueue, AdminQueueJob } from "@/lib/api";
import {
  cancelJob,
  clearStoredAdminToken,
  getAdminQueue,
  isAdminAuthError,
  recoverStaleJobs,
  setStoredAdminToken
} from "@/lib/api";

function formatDate(value?: string | null): string {
  if (!value) return "NA";
  return new Intl.DateTimeFormat("en", {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(new Date(value));
}

function formatQueue(queue: AdminQueueJob["queue"]): string {
  if (queue.position === 0) return "running";
  if (typeof queue.position === "number") return `#${queue.position}`;
  return "-";
}

export default function AdminQueuePage() {
  const [queue, setQueue] = useState<AdminQueue | null>(null);
  const [error, setError] = useState("");
  const [busyJob, setBusyJob] = useState<string | null>(null);
  const [recovering, setRecovering] = useState(false);
  const [needsPassword, setNeedsPassword] = useState(false);

  const handleError = useCallback((err: unknown, fallback: string): boolean => {
    if (isAdminAuthError(err)) {
      clearStoredAdminToken();
      setQueue(null);
      setNeedsPassword(true);
      setError("Enter the admin password to continue.");
      return true;
    }
    setError(err instanceof Error ? err.message : fallback);
    return false;
  }, []);

  const load = useCallback(async () => {
    try {
      setQueue(await getAdminQueue());
      setNeedsPassword(false);
      setError("");
    } catch (err) {
      handleError(err, "Failed to load queue");
    }
  }, [handleError]);

  useEffect(() => {
    if (needsPassword) return;
    let active = true;
    let timer: number | undefined;
    async function poll() {
      try {
        const data = await getAdminQueue();
        if (!active) return;
        setQueue(data);
        setError("");
      } catch (err) {
        if (active && handleError(err, "Failed to load queue")) return;
      }
      if (active) timer = window.setTimeout(poll, 3000);
    }
    poll();
    return () => {
      active = false;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [handleError, needsPassword]);

  async function onCancel(jobId: string) {
    setBusyJob(jobId);
    try {
      await cancelJob(jobId);
      await load();
    } catch (err) {
      handleError(err, "Failed to cancel job");
    } finally {
      setBusyJob(null);
    }
  }

  async function onRecoverStale() {
    setRecovering(true);
    try {
      await recoverStaleJobs();
      await load();
    } catch (err) {
      handleError(err, "Failed to recover stale jobs");
    } finally {
      setRecovering(false);
    }
  }

  function onUnlock(password: string) {
    setStoredAdminToken(password);
    setNeedsPassword(false);
    setError("");
    void load();
  }

  if (needsPassword) {
    return <AdminPasswordPanel error={error} onSubmit={onUnlock} />;
  }
  if (error && !queue) {
    return <section className="panel pad error">{error}</section>;
  }
  if (!queue) {
    return <section className="panel pad">Loading queue...</section>;
  }

  return (
    <>
      <section className="page-title">
        <div>
          <p className="eyebrow">Admin</p>
          <h2>Job queue</h2>
          <p>Queued, running, and recent jobs for this local ANDES server.</p>
        </div>
        <div className="page-actions">
          <button
            className="button secondary"
            disabled={recovering}
            type="button"
            onClick={onRecoverStale}
          >
            {recovering ? "Recovering..." : "Recover stale"}
          </button>
          <Link className="button secondary" href="/admin">
            Server status
          </Link>
        </div>
      </section>

      {error ? <section className="panel pad error">{error}</section> : null}

      <section className="summary-grid" aria-label="Queue summary">
        <div className="summary-card">
          <strong>{queue.stats.running ?? 0}</strong>
          <span>running</span>
        </div>
        <div className="summary-card">
          <strong>{queue.stats.queued ?? 0}</strong>
          <span>queued</span>
        </div>
        <div className="summary-card">
          <strong>{queue.limits.max_queued_jobs}</strong>
          <span>queue cap</span>
        </div>
        <div className="summary-card">
          <strong>{queue.limits.max_jobs_per_owner}</strong>
          <span>per client cap</span>
        </div>
      </section>

      <section className="panel pad">
        <div className="section-head">
          <div>
            <p className="eyebrow">Queue</p>
            <h2>Jobs</h2>
          </div>
          <span className="subtle">
            stale timeout {queue.limits.running_job_timeout_seconds}s
          </span>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Job</th>
                <th>State</th>
                <th>Queue</th>
                <th>Owner</th>
                <th>Created</th>
                <th>Started</th>
                <th>Finished</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {queue.jobs.map((job) => (
                <tr key={job.id}>
                  <td className="term-cell">
                    <strong>
                      <Link href={`/jobs/${job.id}`}>{job.id.slice(0, 10)}</Link>
                    </strong>
                    <span>{job.kind.replace("_", " ")}</span>
                  </td>
                  <td>
                    <span className={`status ${job.state}`}>{job.state}</span>
                  </td>
                  <td>{formatQueue(job.queue)}</td>
                  <td className="mono">{job.owner_key ?? "unknown"}</td>
                  <td>{formatDate(job.created_at)}</td>
                  <td>{formatDate(job.started_at)}</td>
                  <td>{formatDate(job.finished_at)}</td>
                  <td>
                    {job.state === "queued" || job.state === "running" ? (
                      <button
                        className="button secondary compact danger"
                        disabled={busyJob === job.id}
                        type="button"
                        onClick={() => onCancel(job.id)}
                      >
                        {busyJob === job.id ? "Cancelling" : "Cancel"}
                      </button>
                    ) : (
                      <span className="subtle">-</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
