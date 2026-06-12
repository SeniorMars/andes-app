export type JobState = "queued" | "running" | "succeeded" | "failed" | "cancelled";
export type AnalysisKind = "set_similarity" | "gsea";

export interface JobRecord {
  id: string;
  kind: AnalysisKind;
  state: JobState;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  cancelled_at?: string | null;
  error?: string | null;
  owner_key?: string | null;
}

export interface ResultTerm {
  term: string;
  description?: string | null;
  size?: number | null;
  query_term?: string | null;
  query_description?: string | null;
  query_size?: number | null;
  target_term?: string | null;
  target_description?: string | null;
  target_size?: number | null;
  true_score?: number | null;
  z_score: number;
  p_value: number;
  p_value_corrected: number;
  log10_p_value_corrected: number;
  significant: boolean;
}

export interface AnalysisResult {
  kind: AnalysisKind;
  results: ResultTerm[];
  input_gene_count: number;
  valid_gene_count: number;
  invalid_genes: string[];
  warnings: string[];
  parameters: Record<string, unknown>;
}

export interface JobResponse {
  job: JobRecord;
  result?: AnalysisResult | null;
  queue?: QueueStatus;
}

export interface QueueStatus {
  state?: JobState | null;
  position?: number | null;
  queued_ahead: number;
}

export interface GenePreview {
  input_count: number;
  matched_count: number;
  unmatched_count: number;
  unmatched_examples: string[];
  id_type_counts: Record<string, number>;
}

export interface CollectionPreview {
  term_count: number;
  usable_term_count: number;
  gene_count: number;
  matched_gene_count: number;
  min_usable_size?: number | null;
  max_usable_size?: number | null;
}

export interface CachePreview {
  kind: string;
  status: "build" | "reuse" | "extend_or_rebuild" | string;
  hit: boolean;
  path: string;
  file?: string;
  seed?: number;
  seed_strategy?: string;
  requested_size_pairs?: number;
  missing_size_pairs?: number;
  added_size_pairs?: number;
  requested_sizes?: number;
  missing_sizes?: number;
  added_sizes?: number;
  metadata_ok?: boolean;
  reason?: string;
  cache_seconds?: number;
}

export interface JobPreview {
  kind: AnalysisKind;
  mode: "gene_list" | "gene_set_collection" | "ranked_enrichment" | string;
  can_submit: boolean;
  over_limit: boolean;
  max_term_pairs: number;
  estimated_pair_count: number;
  genes?: GenePreview;
  query_collection?: CollectionPreview;
  target_collection?: CollectionPreview;
  cache: CachePreview;
  warnings: string[];
}

export interface DataStatus {
  ready: boolean;
  checks: Record<string, boolean>;
  cache: {
    root: string;
    exists: boolean;
    bma: CacheDirectoryStatus;
    es: CacheDirectoryStatus;
  };
  jobs: {
    sqlite_path: string;
    runs_dir: string;
    job_counts: Record<JobState, number>;
    run_directories: number;
    run_bytes: number;
  };
  config: Record<string, string | number | boolean | null>;
}

export interface AdminQueueJob extends JobRecord {
  queue: QueueStatus;
}

export interface AdminQueue {
  stats: Record<JobState, number>;
  limits: {
    max_queued_jobs: number;
    max_jobs_per_owner: number;
    running_job_timeout_seconds: number;
  };
  jobs: AdminQueueJob[];
}

export interface StaleRecoveryResult {
  recovered_jobs: number;
  recovered_ids: string[];
}

export interface CacheDirectoryStatus {
  path: string;
  exists: boolean;
  files: number;
  bytes: number;
  newest_mtime?: number | null;
}

const CONFIGURED_API_BASE = process.env.NEXT_PUBLIC_API_URL;

function getApiBase(): string {
  if (CONFIGURED_API_BASE) {
    return CONFIGURED_API_BASE.replace(/\/$/, "");
  }
  if (typeof window !== "undefined") {
    const hostname =
      window.location.hostname === "0.0.0.0" || window.location.hostname === "::"
        ? "localhost"
        : window.location.hostname;
    return `${window.location.protocol}//${hostname}:8000`;
  }
  return "http://localhost:8000";
}

function getFallbackApiBases(primary: string): string[] {
  if (CONFIGURED_API_BASE || typeof window === "undefined") {
    return [primary];
  }
  const candidates = [
    primary,
    `${window.location.protocol}//localhost:8000`,
    `${window.location.protocol}//127.0.0.1:8000`,
  ];
  return [...new Set(candidates)];
}

async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const apiBase = getApiBase();
  const attempted: string[] = [];
  for (const candidate of getFallbackApiBases(apiBase)) {
    attempted.push(candidate);
    try {
      return await fetch(`${candidate}${path}`, init);
    } catch {
      continue;
    }
  }
  throw new Error(
    `Could not reach ANDES API. Tried ${attempted.join(", ")}. Start the backend with uv run andes-api.`
  );
}

function buildFormData(
  fieldName: string,
  text: string,
  fields: Record<string, string | number | undefined>,
  files: Record<string, File | null | undefined>
): FormData {
  const body = new FormData();
  body.append(fieldName, text);
  for (const [key, value] of Object.entries(fields)) {
    if (value !== undefined && value !== "") {
      body.append(key, String(value));
    }
  }
  for (const [key, file] of Object.entries(files)) {
    if (file) {
      body.append(key, file);
    }
  }
  return body;
}

async function readApiError(response: Response): Promise<string> {
  const text = await response.text();
  try {
    const parsed = JSON.parse(text) as { detail?: unknown };
    if (typeof parsed.detail === "string") {
      return parsed.detail;
    }
    if (parsed.detail && typeof parsed.detail === "object") {
      return JSON.stringify(parsed.detail);
    }
  } catch {
    // Fall through to raw text.
  }
  return text || `${response.status} ${response.statusText}`;
}

export async function submitTextJob(
  path: string,
  fieldName: string,
  text: string,
  fields: Record<string, string | number | undefined> = {},
  files: Record<string, File | null | undefined> = {}
): Promise<JobRecord> {
  const body = buildFormData(fieldName, text, fields, files);
  const response = await apiFetch(path, {
    method: "POST",
    body
  });
  if (!response.ok) {
    throw new Error(await readApiError(response));
  }
  return response.json();
}

export async function previewTextJob(
  path: string,
  fieldName: string,
  text: string,
  fields: Record<string, string | number | undefined> = {},
  files: Record<string, File | null | undefined> = {}
): Promise<JobPreview> {
  const body = buildFormData(fieldName, text, fields, files);
  const response = await apiFetch(path, {
    method: "POST",
    body
  });
  if (!response.ok) {
    throw new Error(await readApiError(response));
  }
  return response.json();
}

export async function getJob(jobId: string): Promise<JobResponse> {
  const response = await apiFetch(`/jobs/${jobId}`, {
    cache: "no-store"
  });
  if (!response.ok) {
    throw new Error(await readApiError(response));
  }
  return response.json();
}

export async function cancelJob(jobId: string): Promise<JobResponse> {
  const response = await apiFetch(`/jobs/${jobId}/cancel`, {
    method: "POST"
  });
  if (!response.ok) {
    throw new Error(await readApiError(response));
  }
  return response.json();
}

export async function getDataStatus(): Promise<DataStatus> {
  const response = await apiFetch("/data/status", {
    cache: "no-store"
  });
  if (!response.ok) {
    throw new Error(await readApiError(response));
  }
  return response.json();
}

export async function getAdminQueue(): Promise<AdminQueue> {
  const response = await apiFetch("/admin/queue", {
    cache: "no-store"
  });
  if (!response.ok) {
    throw new Error(await readApiError(response));
  }
  return response.json();
}

export async function recoverStaleJobs(): Promise<StaleRecoveryResult> {
  const response = await apiFetch("/admin/queue/recover-stale", {
    method: "POST"
  });
  if (!response.ok) {
    throw new Error(await readApiError(response));
  }
  return response.json();
}

export function getDownloadUrl(jobId: string, filename: string): string {
  return `${getApiBase()}/jobs/${jobId}/download/${filename}`;
}
