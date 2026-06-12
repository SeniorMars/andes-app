# ANDES App v2

Prototype rebuild of the ANDES web app with a tested Python core, a thin FastAPI API,
a single local worker, and a TypeScript frontend.

## Shape

```text
v2/
  backend/
    src/andes_core/    pure wrapper around optimized ANDES code
    src/andes_api/     FastAPI app and SQLite job store
    src/andes_worker/  one-process worker loop
  web/                 Next.js TypeScript UI
```

The current design intentionally skips Redis/Celery. One queued job runs at a
time by default, and that job can use `ANDES_WORKERS=8` internally.
The web app does not let users choose worker count or null-sampling iterations;
those are server-owned settings so expensive cache files remain reusable.

## Backend

Use `uv` for all Python work.

```bash
cd v2/backend
uv sync
uv run pytest
uv run andes validate-data
```

Run the API:

```bash
cd v2/backend
uv run andes-api
```

Run the worker in another terminal:

```bash
cd v2/backend
uv run andes-worker
```

Useful environment variables:

```bash
ANDES_ORIGINAL_SRC=/Users/charlie/Acdemica/ylab/ANDES/src
ANDES_EMBEDDING_PATH=/Users/charlie/Acdemica/ylab/ANDES/data/embedding/node2vec_consensus.csv
ANDES_GENE_LIST_PATH=/Users/charlie/Acdemica/ylab/ANDES/data/embedding/consensus_node.txt
ANDES_DEFAULT_GENE_SET_PATH=/Users/charlie/Acdemica/ylab/ANDES/data/gene_sets/hsa_experimental_eval_BP_propagated.gmt
ANDES_WORKERS=8
ANDES_NULL_ITERATIONS=1000
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
ANDES_ALIAS_PATH=/path/to/gene_aliases.tsv
```

See `.env.example` for the same settings in an environment-file format suitable
for Docker Compose, systemd, or launchd.

## Frontend

```bash
cd v2/web
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
`/Users/charlie/Acdemica/ylab/ANDES/benchmarks/run_benchmarks.sh`:

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
disabled until the current inputs have a successful preview.

Large jobs are blocked before queueing when they exceed
`ANDES_MAX_TERM_PAIRS`. Collection-vs-collection set similarity can grow
quickly because it scores `query_terms * target_terms`; keep
`ANDES_ALLOW_LARGE_JOBS=false` on shared servers unless an admin explicitly
wants to bypass the limit. `ANDES_MAX_UPLOAD_BYTES` and
`ANDES_MAX_TERMS_PER_COLLECTION` protect upload size and parser load.

Gene IDs are mapped to the configured embedding gene IDs before validation.
Direct matches to `ANDES_GENE_LIST_PATH` always work. If `ANDES_ALIAS_PATH` is
set, it should point to a TSV or CSV file where each row contains the canonical
embedding gene ID plus one or more aliases. Headers such as `alias,gene` are
ignored. The app detects common submitted ID shapes, including Entrez-like
numbers, Ensembl IDs, UniProt-like IDs, and symbol-like values. Result JSON keeps
the submitted ID, mapped ID, detected type, and mapping source.

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

The API stores a simple owner key for queue limiting. If callers provide
`X-ANDES-USER`, that value is used; otherwise the client IP is used. New jobs are
rejected with HTTP 429 when `ANDES_MAX_QUEUED_JOBS` or
`ANDES_MAX_JOBS_PER_OWNER` is exceeded. Job pages show queue position for queued
jobs and expose a cancel button. Cancelling a queued job is immediate; cancelling
a running job is best-effort and prevents the worker from writing success after
the current ANDES call returns.

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
`v2/cache` when the backend is run from `v2/backend`.

```text
cache/
  bma/  # set-similarity null caches
  es/   # ranked enrichment null caches
```

The cache is checked after input validation. If none of the submitted genes are
present in `consensus_node.txt`, the job fails before any cache is loaded or
built.

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

Per-job cache status is also shown in preflight. A `reuse` result means the
needed null buckets already exist; `build` means no matching cache file exists;
`extend_or_rebuild` means the file exists but some requested size buckets or
metadata are missing.

Cache files are touched whenever they are reused, so file modification time is
treated as "last used". Prune cold cache files with:

```bash
cd v2/backend
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

Docker Compose is available from `v2/`:

```bash
cd v2
cp .env.example .env
docker compose up --build
```

The compose stack runs four services:

- `api`: FastAPI on port 8000.
- `worker`: queued job processor using the shared SQLite/runs directory.
- `web`: Next.js on port 3000.
- `cleanup`: one-shot maintenance service under the `ops` profile.

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
