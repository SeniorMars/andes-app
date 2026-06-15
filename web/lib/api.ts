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
  access_token?: string;
}

export interface JobHistoryEntry {
  id: string;
  kind: AnalysisKind;
  state?: JobState;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  cancelled_at?: string | null;
  error?: string | null;
  access_token?: string;
  label?: string;
  notes?: string;
  created_local_at: string;
  last_seen_at: string;
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
  path?: string;
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
  preview_digest?: string;
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
    exists: boolean;
    bma: CacheDirectoryStatus;
    es: CacheDirectoryStatus;
  };
  jobs: {
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
  exists: boolean;
  files: number;
  bytes: number;
  newest_mtime?: number | null;
}

const CONFIGURED_API_BASE = process.env.NEXT_PUBLIC_API_URL;
const ADMIN_TOKEN_STORAGE_KEY = "andes.adminToken";
const JOB_TOKENS_STORAGE_KEY = "andes.jobTokens.v1";
const JOB_HISTORY_STORAGE_KEY = "andes.jobHistory.v1";
const MAX_JOB_HISTORY_ENTRIES = 200;

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function safeLocalStorageGet(key: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeLocalStorageSet(key: string, value: string): boolean {
  if (typeof window === "undefined") return false;
  try {
    window.localStorage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

function safeSessionStorageGet(key: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSessionStorageSet(key: string, value: string): boolean {
  if (typeof window === "undefined") return false;
  try {
    window.sessionStorage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

function safeSessionStorageRemove(key: string): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.removeItem(key);
  } catch {
    // Storage can be unavailable in private or embedded browsing contexts.
  }
}

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

function hasSensitiveHeaders(init?: RequestInit): boolean {
  const headers = new Headers(init?.headers);
  return (
    headers.has("x-andes-admin-token") ||
    headers.has("x-andes-job-token") ||
    headers.has("authorization")
  );
}

function isAbortError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    error.name === "AbortError"
  );
}

async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const apiBase = getApiBase();
  const candidates = hasSensitiveHeaders(init) ? [apiBase] : getFallbackApiBases(apiBase);
  const attempted: string[] = [];
  for (const candidate of candidates) {
    attempted.push(candidate);
    try {
      return await fetch(`${candidate}${path}`, init);
    } catch (err) {
      if (isAbortError(err)) {
        throw err;
      }
      continue;
    }
  }
  throw new Error(
    `Could not reach ANDES API. Tried ${attempted.join(", ")}. Start the backend with uv run andes-api.`
  );
}

function getSessionAdminToken(): string | null {
  return safeSessionStorageGet(ADMIN_TOKEN_STORAGE_KEY);
}

function getAdminToken(): string | null {
  return getSessionAdminToken();
}

export function setStoredAdminToken(token: string): void {
  if (typeof window === "undefined") return;
  const trimmed = token.trim();
  if (trimmed) {
    safeSessionStorageSet(ADMIN_TOKEN_STORAGE_KEY, trimmed);
  }
}

export function clearStoredAdminToken(): void {
  safeSessionStorageRemove(ADMIN_TOKEN_STORAGE_KEY);
}

export function isAdminAuthError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403;
}

function withAdminToken(init: RequestInit = {}): RequestInit {
  const token = getAdminToken();
  if (!token) return init;
  const headers = new Headers(init.headers);
  headers.set("x-andes-admin-token", token);
  return {
    ...init,
    headers
  };
}

function readStoredJobTokens(): Record<string, string> {
  const raw = safeLocalStorageGet(JOB_TOKENS_STORAGE_KEY);
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return {};
    }
    return Object.fromEntries(
      Object.entries(parsed).filter(
        (entry): entry is [string, string] =>
          typeof entry[0] === "string" && typeof entry[1] === "string"
      )
    );
  } catch {
    return {};
  }
}

function isJobState(value: unknown): value is JobState {
  return (
    value === "queued" ||
    value === "running" ||
    value === "succeeded" ||
    value === "failed" ||
    value === "cancelled"
  );
}

function isAnalysisKind(value: unknown): value is AnalysisKind {
  return value === "set_similarity" || value === "gsea";
}

function stringOrNull(value: unknown): string | null | undefined {
  if (value === null) return null;
  return typeof value === "string" ? value : undefined;
}

function coerceHistoryEntry(value: unknown): JobHistoryEntry | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  const id = typeof record.id === "string" ? record.id : null;
  const kind = isAnalysisKind(record.kind) ? record.kind : null;
  if (!id || !kind) return null;
  const now = new Date().toISOString();
  return {
    id,
    kind,
    state: isJobState(record.state) ? record.state : undefined,
    created_at: stringOrNull(record.created_at),
    started_at: stringOrNull(record.started_at),
    finished_at: stringOrNull(record.finished_at),
    cancelled_at: stringOrNull(record.cancelled_at),
    error: stringOrNull(record.error),
    access_token: typeof record.access_token === "string" ? record.access_token : undefined,
    label: typeof record.label === "string" ? record.label : "",
    notes: typeof record.notes === "string" ? record.notes : "",
    created_local_at:
      typeof record.created_local_at === "string" ? record.created_local_at : now,
    last_seen_at: typeof record.last_seen_at === "string" ? record.last_seen_at : now
  };
}

function readStoredJobHistoryMap(): Record<string, JobHistoryEntry> {
  const raw = safeLocalStorageGet(JOB_HISTORY_STORAGE_KEY);
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw) as unknown;
    const values =
      parsed && typeof parsed === "object" && !Array.isArray(parsed)
        ? Object.values(parsed)
        : Array.isArray(parsed)
          ? parsed
          : [];
    return Object.fromEntries(
      values
        .map(coerceHistoryEntry)
        .filter((entry): entry is JobHistoryEntry => entry !== null)
        .map((entry) => [entry.id, entry])
    );
  } catch {
    return {};
  }
}

function writeStoredJobHistoryMap(history: Record<string, JobHistoryEntry>): void {
  const entries = Object.values(history)
    .sort((a, b) => (b.created_at ?? b.created_local_at).localeCompare(a.created_at ?? a.created_local_at))
    .slice(0, MAX_JOB_HISTORY_ENTRIES);
  safeLocalStorageSet(
    JOB_HISTORY_STORAGE_KEY,
    JSON.stringify(Object.fromEntries(entries.map((entry) => [entry.id, entry])))
  );
}

export function getStoredJobHistory(): JobHistoryEntry[] {
  return Object.values(readStoredJobHistoryMap()).sort((a, b) =>
    (b.created_at ?? b.created_local_at).localeCompare(a.created_at ?? a.created_local_at)
  );
}

function jobHistoryChanged(existing: JobHistoryEntry, next: JobHistoryEntry): boolean {
  return (
    existing.kind !== next.kind ||
    existing.state !== next.state ||
    existing.created_at !== next.created_at ||
    existing.started_at !== next.started_at ||
    existing.finished_at !== next.finished_at ||
    existing.cancelled_at !== next.cancelled_at ||
    existing.error !== next.error ||
    existing.access_token !== next.access_token
  );
}

export function upsertStoredJobHistory(job: JobRecord, token?: string): JobHistoryEntry | null {
  if (typeof window === "undefined") return null;
  const history = readStoredJobHistoryMap();
  const existing = history[job.id];
  const now = new Date().toISOString();
  const accessToken = token?.trim() || job.access_token || existing?.access_token;
  const nextEntry: JobHistoryEntry = {
    id: job.id,
    kind: job.kind,
    state: job.state,
    created_at: job.created_at,
    started_at: job.started_at,
    finished_at: job.finished_at,
    cancelled_at: job.cancelled_at,
    error: job.error,
    access_token: accessToken,
    label: existing?.label ?? "",
    notes: existing?.notes ?? "",
    created_local_at: existing?.created_local_at ?? now,
    last_seen_at: now
  };
  if (existing && !jobHistoryChanged(existing, nextEntry)) {
    return existing;
  }
  history[job.id] = nextEntry;
  writeStoredJobHistoryMap(history);
  if (accessToken && accessToken !== existing?.access_token) {
    setStoredJobAccessToken(job.id, accessToken);
  }
  return nextEntry;
}

export function updateStoredJobHistoryEntry(
  jobId: string,
  patch: Partial<Pick<JobHistoryEntry, "label" | "notes">>
): JobHistoryEntry | null {
  if (typeof window === "undefined") return null;
  const history = readStoredJobHistoryMap();
  const entry = history[jobId];
  if (!entry) return null;
  history[jobId] = {
    ...entry,
    ...patch,
    last_seen_at: new Date().toISOString()
  };
  writeStoredJobHistoryMap(history);
  return history[jobId];
}

export function removeStoredJobHistoryEntry(jobId: string): void {
  const history = readStoredJobHistoryMap();
  delete history[jobId];
  writeStoredJobHistoryMap(history);
  const tokens = readStoredJobTokens();
  delete tokens[jobId];
  safeLocalStorageSet(JOB_TOKENS_STORAGE_KEY, JSON.stringify(tokens));
}

export function getStoredJobAccessToken(jobId: string): string | null {
  return readStoredJobTokens()[jobId] ?? null;
}

export function setStoredJobAccessToken(jobId: string, token: string): void {
  const trimmed = token.trim();
  if (!jobId || !trimmed) return;
  const tokens = readStoredJobTokens();
  tokens[jobId] = trimmed;
  safeLocalStorageSet(JOB_TOKENS_STORAGE_KEY, JSON.stringify(tokens));
  const history = readStoredJobHistoryMap();
  if (history[jobId]) {
    history[jobId] = {
      ...history[jobId],
      access_token: trimmed,
      last_seen_at: new Date().toISOString()
    };
    writeStoredJobHistoryMap(history);
  }
}

function withJobToken(jobId: string, init: RequestInit = {}, token?: string): RequestInit {
  const jobToken = token?.trim() || getStoredJobAccessToken(jobId);
  if (!jobToken) return init;
  const headers = new Headers(init.headers);
  headers.set("x-andes-job-token", jobToken);
  return {
    ...init,
    headers
  };
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

async function throwApiError(response: Response): Promise<never> {
  throw new ApiError(response.status, await readApiError(response));
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
    await throwApiError(response);
  }
  const job = (await response.json()) as JobRecord;
  upsertStoredJobHistory(job, job.access_token);
  return job;
}

export async function previewTextJob(
  path: string,
  fieldName: string,
  text: string,
  fields: Record<string, string | number | undefined> = {},
  files: Record<string, File | null | undefined> = {},
  options: { signal?: AbortSignal } = {}
): Promise<JobPreview> {
  const body = buildFormData(fieldName, text, fields, files);
  const response = await apiFetch(path, {
    method: "POST",
    body,
    signal: options.signal
  });
  if (!response.ok) {
    await throwApiError(response);
  }
  return response.json();
}

export async function getJob(jobId: string, token?: string): Promise<JobResponse> {
  const response = await apiFetch(`/jobs/${jobId}`, {
    ...withJobToken(
      jobId,
      withAdminToken({
        cache: "no-store"
      }),
      token
    )
  });
  if (!response.ok) {
    await throwApiError(response);
  }
  const payload = (await response.json()) as JobResponse;
  upsertStoredJobHistory(payload.job, token);
  return payload;
}

export async function cancelJob(jobId: string, token?: string): Promise<JobResponse> {
  const response = await apiFetch(
    `/jobs/${jobId}/cancel`,
    withJobToken(
      jobId,
      withAdminToken({
        method: "POST"
      }),
      token
    )
  );
  if (!response.ok) {
    await throwApiError(response);
  }
  const payload = (await response.json()) as JobResponse;
  upsertStoredJobHistory(payload.job, token);
  return payload;
}

export async function rerunJob(jobId: string, token?: string): Promise<JobRecord> {
  const response = await apiFetch(
    `/jobs/${jobId}/rerun`,
    withJobToken(
      jobId,
      withAdminToken({
        method: "POST"
      }),
      token
    )
  );
  if (!response.ok) {
    await throwApiError(response);
  }
  const job = (await response.json()) as JobRecord;
  upsertStoredJobHistory(job, job.access_token);
  return job;
}

export async function getDataStatus(): Promise<DataStatus> {
  const response = await apiFetch(
    "/data/status",
    withAdminToken({
      cache: "no-store"
    })
  );
  if (!response.ok) {
    await throwApiError(response);
  }
  return response.json();
}

export async function getAdminQueue(): Promise<AdminQueue> {
  const response = await apiFetch(
    "/admin/queue",
    withAdminToken({
      cache: "no-store"
    })
  );
  if (!response.ok) {
    await throwApiError(response);
  }
  return response.json();
}

export async function recoverStaleJobs(): Promise<StaleRecoveryResult> {
  const response = await apiFetch(
    "/admin/queue/recover-stale",
    withAdminToken({
      method: "POST"
    })
  );
  if (!response.ok) {
    await throwApiError(response);
  }
  return response.json();
}

function filenameFromContentDisposition(value: string | null, fallback: string): string {
  if (!value) return fallback;
  const utf8Match = value.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match?.[1]) {
    try {
      return decodeURIComponent(utf8Match[1]);
    } catch {
      return fallback;
    }
  }
  const asciiMatch = value.match(/filename="?([^";]+)"?/i);
  return asciiMatch?.[1] ?? fallback;
}

export async function downloadJobArtifact(
  jobId: string,
  filename: string,
  token?: string
): Promise<{ blob: Blob; filename: string }> {
  const response = await apiFetch(
    `/jobs/${jobId}/download/${encodeURIComponent(filename)}`,
    withJobToken(jobId, withAdminToken({}), token)
  );
  if (!response.ok) {
    await throwApiError(response);
  }
  return {
    blob: await response.blob(),
    filename: filenameFromContentDisposition(
      response.headers.get("content-disposition"),
      filename
    )
  };
}
