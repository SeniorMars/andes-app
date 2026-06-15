"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import type { JobHistoryEntry } from "@/lib/api";
import {
  getJob,
  getStoredJobHistory,
  removeStoredJobHistoryEntry,
  rerunJob,
  updateStoredJobHistoryEntry
} from "@/lib/api";

function formatDate(value?: string | null): string {
  if (!value) return "Not finished";
  return new Intl.DateTimeFormat("en", {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(new Date(value));
}

function kindLabel(kind: JobHistoryEntry["kind"]): string {
  return kind === "set_similarity" ? "Set similarity" : "GSEA";
}

function sortHistory(entries: JobHistoryEntry[]): JobHistoryEntry[] {
  return [...entries].sort((a, b) =>
    (b.created_at ?? b.created_local_at).localeCompare(a.created_at ?? a.created_local_at)
  );
}

export default function JobsPage() {
  const router = useRouter();
  const [entries, setEntries] = useState<JobHistoryEntry[]>([]);
  const [syncing, setSyncing] = useState(false);
  const [syncErrors, setSyncErrors] = useState<Record<string, string>>({});
  const [rerunningId, setRerunningId] = useState<string | null>(null);

  function reloadLocalHistory() {
    setEntries(sortHistory(getStoredJobHistory()));
  }

  async function syncHistory(isActive: () => boolean = () => true) {
    const localEntries = sortHistory(getStoredJobHistory());
    if (!isActive()) return;
    setEntries(localEntries);
    setSyncErrors({});
    if (!localEntries.length) return;
    setSyncing(true);
    const nextErrors: Record<string, string> = {};
    try {
      await Promise.all(
        localEntries.map(async (entry) => {
          try {
            await getJob(entry.id, entry.access_token);
          } catch (err) {
            nextErrors[entry.id] =
              err instanceof Error ? err.message : "Could not refresh this job";
          }
        })
      );
      if (!isActive()) return;
      setSyncErrors(nextErrors);
      setEntries(sortHistory(getStoredJobHistory()));
    } finally {
      if (isActive()) setSyncing(false);
    }
  }

  useEffect(() => {
    let active = true;
    syncHistory(() => active);
    return () => {
      active = false;
    };
  }, []);

  function updateEntry(jobId: string, patch: Partial<Pick<JobHistoryEntry, "label" | "notes">>) {
    const updated = updateStoredJobHistoryEntry(jobId, patch);
    if (!updated) return;
    setEntries((current) =>
      sortHistory(current.map((entry) => (entry.id === jobId ? updated : entry)))
    );
  }

  function removeEntry(jobId: string) {
    removeStoredJobHistoryEntry(jobId);
    setEntries((current) => current.filter((entry) => entry.id !== jobId));
  }

  async function onRerun(entry: JobHistoryEntry) {
    setRerunningId(entry.id);
    try {
      const job = await rerunJob(entry.id, entry.access_token);
      if (entry.label || entry.notes) {
        updateStoredJobHistoryEntry(job.id, {
          label: entry.label ? `${entry.label} rerun` : "",
          notes: entry.notes ?? ""
        });
      }
      reloadLocalHistory();
      router.push(`/jobs/${job.id}`);
    } catch (err) {
      setSyncErrors((current) => ({
        ...current,
        [entry.id]: err instanceof Error ? err.message : "Could not rerun this job"
      }));
    } finally {
      setRerunningId(null);
    }
  }

  return (
    <>
      <section className="page-title">
        <div>
          <p className="eyebrow">History</p>
          <h2>My jobs</h2>
          <p>Jobs saved in this browser are listed here with their local notes.</p>
        </div>
        <div className="page-actions">
          <button
            className="button secondary"
            disabled={syncing}
            type="button"
            onClick={() => syncHistory()}
          >
            {syncing ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </section>

      {entries.length ? (
        <div className="history-list">
          {entries.map((entry) => (
            <article className="panel pad history-card" key={entry.id}>
              <div className="history-main">
                <div>
                  <p className="eyebrow">{kindLabel(entry.kind)}</p>
                  <h2>{entry.label?.trim() || entry.id}</h2>
                  <p className="job-id">{entry.id}</p>
                </div>
                <span className={`status ${entry.state ?? "queued"}`}>
                  {entry.state ?? "saved"}
                </span>
              </div>

              <div className="history-meta">
                <span>Created {formatDate(entry.created_at ?? entry.created_local_at)}</span>
                <span>Finished {formatDate(entry.finished_at)}</span>
              </div>

              {syncErrors[entry.id] ? <p className="error">{syncErrors[entry.id]}</p> : null}
              {entry.error ? <p className="error">{entry.error}</p> : null}

              <div className="history-fields">
                <label className="field">
                  <span>Label</span>
                  <input
                    value={entry.label ?? ""}
                    onChange={(event) => updateEntry(entry.id, { label: event.target.value })}
                  />
                </label>
                <label className="field">
                  <span>Notes</span>
                  <textarea
                    rows={3}
                    value={entry.notes ?? ""}
                    onChange={(event) => updateEntry(entry.id, { notes: event.target.value })}
                  />
                </label>
              </div>

              <div className="history-actions">
                <Link className="button primary" href={`/jobs/${entry.id}`}>
                  Open
                </Link>
                <button
                  className="button secondary"
                  disabled={rerunningId === entry.id}
                  type="button"
                  onClick={() => onRerun(entry)}
                >
                  {rerunningId === entry.id ? "Queueing..." : "Rerun"}
                </button>
                <button className="button danger" type="button" onClick={() => removeEntry(entry.id)}>
                  Remove
                </button>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <section className="panel pad empty-state">
          <p className="eyebrow">No saved jobs</p>
          <h2>No jobs in this browser yet</h2>
          <p>Run an analysis or open a result link with its access token to populate this page.</p>
          <div className="download-row">
            <Link className="button primary" href="/set-similarity">
              Set Similarity
            </Link>
            <Link className="button secondary" href="/gsea">
              GSEA
            </Link>
          </div>
        </section>
      )}
    </>
  );
}
