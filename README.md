# ANDES App v2

Prototype rebuild of the ANDES web app with a tested Python core, a thin FastAPI API,
a single local worker, and a TypeScript frontend.

## Shape

```text
backend/
  src/andes_core/    pure wrapper around optimized ANDES code
  src/andes_api/     FastAPI app and SQLite job store
  src/andes_worker/  one-process worker loop
web/                 Next.js TypeScript UI
```

The current design intentionally skips Redis/Celery. One queued job runs at a
time by default. Increase `ANDES_JOB_CONCURRENCY` to run more jobs in parallel;
each job can still use `ANDES_WORKERS=8` internally. Approximate compute
pressure is `ANDES_JOB_CONCURRENCY * ANDES_WORKERS`, so
`ANDES_JOB_CONCURRENCY=4` and `ANDES_WORKERS=8` can behave like 32-way
parallelism.
The web app does not let users choose worker count or null-sampling iterations;
those are server-owned settings so expensive cache files remain reusable.

## Backend

Use `uv` for all Python work.

```bash
cd backend
uv sync
uv run python -m pytest
uv run andes validate-data
```

Run the API:

```bash
cd backend
uv run andes-api
```

Run the worker in another terminal:

```bash
cd backend
uv run andes-worker
```

Useful environment variables:

```bash
# Set these paths to your local checkout or mounted copy of the original ANDES data.
ANDES_ORIGINAL_ROOT=/path/to/ANDES
ANDES_ORIGINAL_SRC=/path/to/ANDES/src
# Optional. Prefer this in production when the original ANDES code is packaged
# as an adapter dependency instead of imported from a mutable source checkout.
# The adapter module must expose load_data, func_optimized, and func_gsea.
# ANDES_ORIGINAL_ADAPTER_MODULE=andes_original_adapter
# Optional. Fail startup if the adapter/check-out reports a different revision.
# ANDES_ORIGINAL_REVISION=<git-sha-or-adapter-revision>
ANDES_API_HOST=127.0.0.1
ANDES_API_PORT=8000
ANDES_API_RELOAD=false
ANDES_EMBEDDING_PATH=/path/to/ANDES/data/embedding/node2vec_consensus.csv
ANDES_GENE_LIST_PATH=/path/to/ANDES/data/embedding/consensus_node.txt
ANDES_DEFAULT_GENE_SET_PATH=/path/to/ANDES/data/gene_sets/hsa_experimental_eval_BP_propagated.gmt
# Effective compute pressure is roughly ANDES_JOB_CONCURRENCY * ANDES_WORKERS.
# Keep that product near CPU count unless you intentionally want oversubscription.
ANDES_WORKERS=8
ANDES_JOB_CONCURRENCY=1
ANDES_NULL_ITERATIONS=1000
ANDES_PREVIEW_DIGEST_TTL_SECONDS=900
# Optional. Set this in shared deployments so preview digests survive API restarts
# and validate consistently across multiple API replicas.
# ANDES_PREVIEW_DIGEST_SECRET=change-me-to-a-random-secret
# Optional. Set this in shared deployments to HMAC-hash job access tokens at rest.
# ANDES_TOKEN_HASH_SECRET=change-me-to-another-random-secret
# Optional. Leave unset to derive a deterministic seed per null-cache key.
# ANDES_SEED=12345
ANDES_RUNS_DIR=../runs
ANDES_CACHE_DIR=../cache
ANDES_SQLITE_PATH=../runs/jobs.sqlite3
ANDES_MAX_UPLOAD_BYTES=10000000
ANDES_MAX_TERM_PAIRS=500000
ANDES_MAX_TERMS_PER_COLLECTION=20000
ANDES_ALLOW_LARGE_JOBS=false
ANDES_MAX_QUEUED_JOBS=100
ANDES_MAX_JOBS_PER_OWNER=10
ANDES_RUNNING_JOB_TIMEOUT_SECONDS=21600
ANDES_CACHE_MAX_AGE_DAYS=30
ANDES_CACHE_MIN_KEEP_FILES=8
ANDES_CACHE_MAX_BYTES=0
ANDES_JOB_MAX_AGE_DAYS=30
ANDES_JOB_MIN_KEEP=20
# Optional admin protection for shared or proxied deployments.
# ANDES_ADMIN_TOKEN=change-me
# Optional. Only trust identity headers injected by your reverse proxy.
# ANDES_TRUSTED_USER_HEADER=X-Authenticated-User
ANDES_ALIAS_PATH=/path/to/gene_aliases.tsv
# Preferred on horchata/shared deployments. Builds a local SQLite alias index
# from the /grain mapping TSV and maps submitted IDs to Entrez IDs.
ANDES_SPECIES=hsa
ANDES_GENE_MAPPING_DIR=/grain/resources/gene_mappings/output/current
# Optional override instead of ANDES_GENE_MAPPING_DIR + ANDES_SPECIES.
# ANDES_GENE_MAPPING_PATH=/grain/resources/gene_mappings/output/current/hsa_mapping_all.txt
# ANDES_GENE_MAPPING_SQLITE_PATH=../cache/gene_mappings_hsa.sqlite3
```

See `.env.example` for the same settings in an environment-file format suitable
for Docker Compose, systemd, or launchd.

## Frontend

```bash
cd web
npm install
npm run dev
```

Open `http://localhost:3000`. By default the web app calls the API on the same
hostname at port 8000, so `http://localhost:3000` calls `http://localhost:8000`
and `http://127.250.116.207:3000` calls `http://127.250.116.207:8000`.
Set `NEXT_PUBLIC_API_URL` only when you need to override that:

```bash
NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev
```

`npm run dev` clears only `.next-dev` before starting Next. If you see an
`ENOENT` error for a file under `.next-dev/server/app/...`, stop the current
Next process and run `npm run dev` again.

The form examples are derived from the original benchmark wrapper,
`/path/to/ANDES/benchmarks/run_benchmarks.sh`:

```text
EMB=data/embedding/node2vec_consensus.csv
GENELIST=data/embedding/consensus_node.txt
GMT=data/gene_sets/hsa_experimental_eval_BP_propagated.gmt
RANKEDLIST=data/expression/GSE3467_rank.txt
```

The set-similarity example uses genes from `GO:0043648` in that GMT. The GSEA
example uses the first rows of `GSE3467_rank.txt`. The benchmark script defaults
to 32 workers on a server; v2 keeps the local default at 8 workers.

Both analysis forms accept optional uploads:

- Set similarity: either a gene-list text/CSV file, or a query gene-set
  collection. The target collection can be the configured default or an upload.
- GSEA: a ranked gene-score text/CSV file and a target gene-set collection.
- Gene-set collections can be uploaded as GMT, or as GO OBO plus an annotation
  file.

Uploads are validated before a job is queued. The API checks UTF-8 decoding,
maximum upload size, input gene overlap with the configured embedding gene list,
ranked-score parsing, GMT row structure, duplicate GMT terms, gene overlap in
the GMT file, and whether at least one term survives the current set-size
filters. Uploaded collections are normalized to immutable per-job GMT copies
under that job's run directory before the worker reads them.

Both analysis forms also have a preflight preview. Preview runs the same
normalization and validation as submission, then reports matched and unmatched
genes, usable terms after min/max filters, estimated pair count, and whether the
job will reuse, extend, or build a null cache. The Queue button is intentionally
disabled until the current inputs have a successful preview. Successful previews
also return a signed digest; if submission sends the same digest and the
normalized inputs plus server data fingerprints still match, the API skips the
expensive preview recomputation. If anything changed, submission falls back to a
full preview check. Preview digests expire after
`ANDES_PREVIEW_DIGEST_TTL_SECONDS` seconds, defaulting to 900. Set
`ANDES_PREVIEW_DIGEST_SECRET` in shared deployments so digests remain valid
across API restarts and multiple API replicas; when it is unset, the API uses a
process-local secret and old digests simply fall back to full preview.

Large jobs are blocked before queueing when they exceed
`ANDES_MAX_TERM_PAIRS`. Collection-vs-collection set similarity can grow
quickly because it scores `query_terms * target_terms`; keep
`ANDES_ALLOW_LARGE_JOBS=false` on shared servers unless an admin explicitly
wants to bypass the limit. `ANDES_MAX_UPLOAD_BYTES` and
`ANDES_MAX_TERMS_PER_COLLECTION` protect upload size and parser load.

Gene IDs are mapped before validation. Direct matches to `ANDES_GENE_LIST_PATH`
always work. For the current human embeddings, the gene list is Entrez-keyed, so
shared deployments should set `ANDES_SPECIES=hsa` and `ANDES_GENE_MAPPING_DIR`
to the `/grain` mapping directory, for example
`/grain/resources/gene_mappings/output/current`. The app resolves
`{ANDES_SPECIES}_mapping_all.txt`, so changing species to `mmu`, `dme`, and so
on also changes the mapping file. The canonical namespace is explicit:
`ANDES_CANONICAL_ID_NAMESPACE=entrez` is currently the only supported canonical
ID type. `ANDES_GENE_MAPPING_PATH` remains available as an explicit override.
Species is allowlisted to known organism codes, and the built mapping index must
overlap at least `ANDES_GENE_MAPPING_MIN_OVERLAP` of the configured embedding
gene list. On first use, the app builds
`ANDES_GENE_MAPPING_SQLITE_PATH` as a local SQLite alias index filtered to Entrez
IDs present in the embedding gene list. Submitted symbols, HGNC symbols,
external synonyms, Ensembl IDs with version suffixes, and UniProt identifiers
then resolve to Entrez IDs before queueing.
The API owns a process-local `GeneMappingService`: startup validates or builds
the SQLite index once, requests reuse the cached mapper for the same source
manifest, and rebuilds take a cross-process `.lock` next to the SQLite file.
Run `uv run andes validate-data` during deployment to exercise that same index
validation before starting traffic.

Previews and results include mapping-quality counts by source:
`direct_entrez`, `gene_mapping`, `alias_file`, `unmapped`, and `ambiguous`.
Ambiguous records are not guessed; the mapping record includes candidate Entrez
IDs so users can inspect the conflict. To audit the configured mapping file,
run:

```bash
cd backend
uv run andes validate-gene-mapping
```

Result payloads and exports also include mapping provenance when a mapping file
is configured: species, canonical namespace, mapping file basename, mtime, size,
SHA-256 checksum, selected and ignored source columns, embedding gene-list
metadata, SQLite index basename, alias-file checksum when configured, and alias
row count. Full per-record mapping audits are written to `mapping-report.csv` as
a job artifact; `results.json` keeps the summary counts and provenance. The
report ZIP includes `mapping-provenance.json`; it is also available as a direct
job download.

GSEA results include sampled running-score trace data for the top terms. The
web result page renders this as an exportable SVG with the running ES curve and
best-match score bars, and report ZIPs include `figures/gsea-running-score.svg`
when trace data are available.

`ANDES_ALIAS_PATH` remains available as a simple fallback for small custom
TSV/CSV files where each row contains the canonical embedding gene ID plus one or
more aliases. Result JSON keeps the submitted ID, mapped ID, detected type,
mapping source, source counts, and ambiguous candidates.

GO/OBO support is real ontology support, not file-extension guessing. An OBO
file defines GO terms and parent relationships; it does not contain gene
memberships. The app therefore requires an annotation file with OBO uploads.
Supported annotation inputs are GAF-style rows or simple `gene<TAB>GO_ID` /
`GO_ID<TAB>gene` tables. The converter skips obsolete OBO terms, skips `NOT`
GAF annotations, follows `is_a` and `part_of` parents, defaults to the
`biological_process` namespace, and keeps only genes present in the configured
embedding gene list.

## Job Model

The API writes `runs/<job_id>/input.json` and inserts a SQLite job row. The
worker claims queued jobs, runs ANDES, then writes:

```text
runs/<job_id>/
  input.json
  results.json
  downloads/
    results.csv
    pair-table.csv  # collection similarity only
    matrix.csv      # collection similarity only
  error.txt       # only on failure
```

States are explicit: `queued`, `running`, `succeeded`, `failed`, `cancelled`.
User-submitted runtime fields such as `workers`, `null_iterations`, or `seed`
are ignored by the API. The worker uses server-owned configuration. If
`ANDES_SEED` is unset, v2 derives a deterministic seed from the null-cache
identity, so matching jobs are reproducible and cacheable without sharing one
global Monte Carlo seed. Set `ANDES_SEED` only when an admin intentionally wants
one fixed server seed for every cache.

The API stores a simple owner key for queue limiting. By default the client IP is
used. If `ANDES_TRUSTED_USER_HEADER` is set, only that configured header is
trusted as a user identity; do not point it at a caller-controlled header unless a
reverse proxy authenticates and overwrites it. New jobs are rejected with HTTP 429 when
`ANDES_MAX_QUEUED_JOBS` or
`ANDES_MAX_JOBS_PER_OWNER` is exceeded. Job pages show queue position for queued
jobs and expose a cancel button. Cancelling a queued job is immediate; cancelling
a running job is best-effort and prevents the worker from writing success after
the current ANDES call returns.

The worker processes up to `ANDES_JOB_CONCURRENCY` jobs at a time, and each job
can use up to `ANDES_WORKERS` internal ANDES workers. Effective compute pressure
is therefore approximately `ANDES_JOB_CONCURRENCY * ANDES_WORKERS`. Keep that
product close to the host CPU count for steady shared deployments; the worker
logs a `worker_parallelism_exceeds_cpu` warning on startup when the product is
higher than `os.cpu_count()`. SQLite is put in WAL mode and indexed for queue
claiming, queue ordering, and owner-limit checks. Cache files keep process-level
file locks so parallel jobs do not corrupt shared null caches.
The provided Compose deployment is intended to run one worker process/container
with internal thread concurrency; do not scale worker replicas horizontally until
the queue has worker leases and heartbeats.

New jobs also receive a per-job access token. The API stores only a token hash
and requires the token, either as `X-Andes-Job-Token` or `?token=...`, for job
status, results, downloads, and cancellation unless the caller is an admin. Set
`ANDES_TOKEN_HASH_SECRET` in shared deployments to store HMAC-SHA256 token hashes
keyed by a server-side pepper; when it is unset, local development falls back to
plain SHA-256 over the high-entropy token. The web UI stores tokens locally in
the submitting browser. Public result payloads expose basename-only file fields
and omit server filesystem paths.

Download URLs:

```text
/jobs/<job_id>/download/results.json
/jobs/<job_id>/download/results.csv
/jobs/<job_id>/download/pair-table.csv
/jobs/<job_id>/download/matrix.csv
```

For collection-vs-collection set similarity, `results.json` and `results.csv`
contain the ranked top rows used by the UI, while `pair-table.csv` contains the
full pair table and `matrix.csv` contains the full Z-score matrix.

Completed job pages also show cache transparency. The cache panel reports cache
hit/build/extend status, the cache file used, requested and added size pairs (or
ranked enrichment size buckets), and runtime split between cache preparation and
scoring. This same information is stored in `results.json` under
`parameters.cache` and `parameters.timing_seconds`.

The results page includes dependency-free SVG visualizations. Gene-list and GSEA
jobs show a ranked Z-score dot plot. Collection-vs-collection jobs show a
top-pair heatmap and a small term-pair graph. Each figure has an SVG export
button for quick reports or notebooks.

## Caches

ANDES v2 uses lazy null-cache files under `ANDES_CACHE_DIR`, which defaults to
`cache` at the repository root when the backend is run from `backend`.

```text
cache/
  bma/  # set-similarity null caches
  es/   # ranked enrichment null caches
```

The cache is checked after input validation. If none of the submitted genes are
present in `consensus_node.txt`, the job fails before any cache is loaded or
built.

Embedding and gene-list loading is cached in-process by resolved path, mtime,
and size. Repeated previews and matching submissions avoid rereading and
renormalizing the embedding until one of those source files changes.

Set-similarity caches are content-addressed by embedding, background population,
Monte Carlo iterations, effective seed, and null mode. By default the effective
seed is derived from the cache identity; if `ANDES_SEED` is configured, that
server seed is used instead. Entries inside the cache are keyed by gene-set size
pairs `(query_size, term_size)`. A later query with the same configuration and
covered size pairs reuses the file; missing size pairs are added and the cache is
saved again.

Ranked GSEA caches are also content-addressed by the ranked list itself, because
the null depends on the ranked embedding. Re-running the same ranked list reuses
the cache; a different ranked list builds a different ES cache.

Check cache status:

```bash
curl http://localhost:8000/data/status
```

The web app exposes the same information at `http://localhost:3000/admin`,
including data-path readiness, cache file counts, job state counts, storage use,
and server-owned limits.

The admin queue page at `http://localhost:3000/admin/queue` shows queued,
running, and recent jobs, queue positions, owner keys, cancellation controls, and
a stale-job recovery action. Workers also recover running jobs older than
`ANDES_RUNNING_JOB_TIMEOUT_SECONDS` before claiming new work.

Admin/status endpoints are available without a token only from loopback clients.
Set `ANDES_ADMIN_TOKEN` before exposing the API through a proxy or non-loopback
bind. The browser admin UI prompts for this value, stores it in session storage,
and sends it as `X-Andes-Admin-Token`. API clients can also send
`Authorization: Bearer <token>`. For a shared web deployment, prefer a
server-side proxy that authenticates the user and injects the admin token. Do
not send the admin token over plain HTTP except on localhost; terminate HTTPS at
the proxy before making the admin UI reachable off-machine.

Per-job cache status is also shown in preflight. A `reuse` result means the
needed null buckets already exist; `build` means no matching cache file exists;
`extend_or_rebuild` means the file exists but some requested size buckets or
metadata are missing.

Cache files are touched whenever they are reused, so file modification time is
treated as "last used". Prune cold cache files with:

```bash
cd backend
uv run andes prune-cache --dry-run
uv run andes prune-cache --max-age-days 30 --min-keep-files 8
```

Prune old completed job run directories with:

```bash
uv run andes prune-jobs --dry-run
uv run andes prune-jobs --max-age-days 30 --min-keep-jobs 20
```

Run both cleanup tasks with:

```bash
uv run andes cleanup --dry-run
uv run andes cleanup
```

For a server, run the combined cleanup command from cron, launchd, or systemd weekly. This is
better than deleting the whole cache on a schedule: recently reused expensive
cache files are preserved, while old files that are no longer helping are
removed. Set `ANDES_CACHE_MAX_BYTES` or pass `--max-bytes` if disk budget should
also cap retention.

The worker writes structured JSON lifecycle logs for `job_started`,
`job_succeeded`, and `job_failed`, including cache profile and timing data for
successful jobs.

## Deployment

Docker Compose is available from the repository root:

```bash
cp .env.example .env
# Edit ANDES_ORIGINAL_ROOT in .env so Compose can mount the original ANDES checkout.
docker compose up --build
```

The compose stack runs four services and publishes web/API ports on host loopback
only by default:

- `api`: FastAPI on port 8000.
- `worker`: queued job processor using the shared SQLite/runs directory.
- `web`: Next.js on port 3000.
- `cleanup`: one-shot maintenance service under the `ops` profile.

The `api`, `worker`, and `web` services use `restart: unless-stopped`. The API
and web services also define healthchecks; the worker waits for the API health
check before starting.

### Local Vs Shared Server

For local use, the loopback port bindings are intentional:

```text
127.0.0.1:8000 -> api
127.0.0.1:3000 -> web
```

This keeps the app reachable only from the machine running Compose. In this mode
you can leave `ANDES_ADMIN_TOKEN` unset, because admin/status endpoints allow
loopback clients without forwarded headers.

For a shared server, keep the Compose services bound to loopback and put an
HTTPS reverse proxy in front of the web/API ports. Set `ANDES_ADMIN_TOKEN` before
exposing the API through that proxy or any non-loopback bind, and send it only
from the trusted server side or admin UI. If the proxy authenticates users and
injects identity headers, set `ANDES_TRUSTED_USER_HEADER` to that proxy-controlled
header name; do not trust caller-supplied identity headers directly.

Run cleanup through Compose with:

```bash
docker compose --profile ops run --rm cleanup
```

Native service templates are also included:

```text
ops/systemd/
  andes-api.service
  andes-worker.service
  andes-cleanup.service
  andes-cleanup.timer
ops/launchd/
  com.andes.api.plist
  com.andes.worker.plist
  com.andes.cleanup.plist
```

Treat these as templates: adjust `WorkingDirectory`, `EnvironmentFile`, and user
paths for the actual server.

## Next Priorities

1. Generate fixed golden outputs from the original ANDES implementation.
2. Add authentication/admin controls before enabling large-job override on a
   shared server.
3. Add more polished report exports if users need publication-ready figures.
