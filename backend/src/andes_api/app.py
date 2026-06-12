from __future__ import annotations

import csv
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from andes_core.config import AndesSettings, get_settings
from andes_core.engine import AndesEngine
from andes_core.io import (
    GeneIdMapper,
    GeneIdMapping,
    go_obo_annotations_to_gmt_text,
    normalize_gmt_text,
    parse_gene_lines,
    parse_ranked_text,
    validate_gmt_text,
)
from andes_core.schemas import AnalysisKind, GseaRequest, SetSimilarityRequest

from .storage import JobStore


def _cache_status(cache_dir):
    root = cache_dir.expanduser().resolve()

    def summarize(name: str):
        directory = root / name
        files = sorted(directory.glob("*.pkl")) if directory.exists() else []
        total_bytes = sum(path.stat().st_size for path in files)
        newest = max((path.stat().st_mtime for path in files), default=None)
        return {
            "path": str(directory),
            "exists": directory.exists(),
            "files": len(files),
            "bytes": total_bytes,
            "newest_mtime": newest,
        }

    return {
        "root": str(root),
        "exists": root.exists(),
        "bma": summarize("bma"),
        "es": summarize("es"),
    }


def _config_status(settings: AndesSettings) -> dict[str, object]:
    return {
        "workers": settings.workers,
        "job_concurrency": settings.job_concurrency,
        "null_iterations": settings.null_iterations,
        "seed": settings.seed,
        "query_memory_mb": settings.query_memory_mb,
        "max_upload_bytes": settings.max_upload_bytes,
        "max_term_pairs": settings.max_term_pairs,
        "max_terms_per_collection": settings.max_terms_per_collection,
        "allow_large_jobs": settings.allow_large_jobs,
        "max_queued_jobs": settings.max_queued_jobs,
        "max_jobs_per_owner": settings.max_jobs_per_owner,
        "running_job_timeout_seconds": settings.running_job_timeout_seconds,
        "cache_max_age_days": settings.cache_max_age_days,
        "cache_min_keep_files": settings.cache_min_keep_files,
        "cache_max_bytes": settings.cache_max_bytes,
        "job_max_age_days": settings.job_max_age_days,
        "job_min_keep": settings.job_min_keep,
        "alias_path": str(settings.alias_path) if settings.alias_path else None,
    }


def _write_csv(path: Path, rows: list[list[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerows(rows)


def _job_result_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = result.get("results")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _materialize_download_from_result(
    store: JobStore,
    job_id: str,
    filename: str,
) -> Path | None:
    result = store.read_result(job_id)
    if result is None:
        return None
    rows = _job_result_rows(result)
    if not rows:
        return None

    downloads = store.run_dir(job_id) / "downloads"
    path = downloads / filename
    if filename == "results.csv":
        _write_csv(
            path,
            [
                [
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
                    "significant",
                ],
                *[
                    [
                        row.get("term", ""),
                        row.get("description", ""),
                        row.get("size", ""),
                        row.get("query_term", ""),
                        row.get("query_description", ""),
                        row.get("query_size", ""),
                        row.get("target_term", ""),
                        row.get("target_description", ""),
                        row.get("target_size", ""),
                        row.get("true_score", ""),
                        row.get("z_score", ""),
                        row.get("p_value", ""),
                        row.get("p_value_corrected", ""),
                        row.get("log10_p_value_corrected", ""),
                        row.get("significant", ""),
                    ]
                    for row in rows
                ],
            ],
        )
        return path

    pair_rows = [row for row in rows if row.get("query_term") and row.get("target_term")]
    if not pair_rows:
        return None
    if filename == "pair-table.csv":
        _write_csv(
            path,
            [
                [
                    "query_term",
                    "query_description",
                    "query_size",
                    "target_term",
                    "target_description",
                    "target_size",
                    "z_score",
                    "p_value",
                    "p_value_corrected",
                ],
                *[
                    [
                        row.get("query_term", ""),
                        row.get("query_description", ""),
                        row.get("query_size", ""),
                        row.get("target_term", ""),
                        row.get("target_description", ""),
                        row.get("target_size", ""),
                        row.get("z_score", ""),
                        row.get("p_value", ""),
                        row.get("p_value_corrected", ""),
                    ]
                    for row in pair_rows
                ],
            ],
        )
        return path

    if filename == "matrix.csv":
        query_terms = list(dict.fromkeys(str(row["query_term"]) for row in pair_rows))
        target_terms = list(dict.fromkeys(str(row["target_term"]) for row in pair_rows))
        score_by_pair = {
            (str(row["query_term"]), str(row["target_term"])): row.get("z_score", "")
            for row in pair_rows
        }
        _write_csv(
            path,
            [
                ["query_term", *target_terms],
                *[
                    [
                        query,
                        *[score_by_pair.get((query, target), "") for target in target_terms],
                    ]
                    for query in query_terms
                ],
            ],
        )
        return path

    return None


async def _read_upload_text(upload: UploadFile, *, label: str, max_bytes: int) -> str:
    contents = await upload.read()
    if len(contents) > max_bytes:
        raise HTTPException(status_code=400, detail=f"{label} is larger than {max_bytes} bytes")
    try:
        return contents.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"{label} must be UTF-8 text") from exc


def _validate_size_range(min_gene_set_size: int, max_gene_set_size: int) -> None:
    if max_gene_set_size < min_gene_set_size:
        raise HTTPException(
            status_code=400,
            detail="max_gene_set_size must be >= min_gene_set_size",
        )


def _validate_uploaded_gmt(
    text: str,
    *,
    known_genes: set[str],
    min_gene_set_size: int,
    max_gene_set_size: int,
    max_terms: int,
) -> None:
    try:
        validate_gmt_text(
            text,
            known_genes=known_genes,
            min_gene_set_size=min_gene_set_size,
            max_gene_set_size=max_gene_set_size,
            max_terms=max_terms,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _mapping_payload(
    mapping: GeneIdMapping,
    *,
    record_limit: int | None = 200,
) -> dict[str, object]:
    records = mapping.records if record_limit is None else mapping.records[:record_limit]
    return {
        "mapped_count": len(mapping.mapped),
        "unmapped_count": len(mapping.unmapped),
        "unmapped_examples": mapping.unmapped[:10],
        "id_type_counts": mapping.id_type_counts,
        "records": [record.__dict__ for record in records],
    }


def _make_mapper(settings: AndesSettings) -> GeneIdMapper:
    try:
        return GeneIdMapper.from_paths(settings.gene_list_path, settings.alias_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


async def _normalize_gene_set_upload(
    *,
    gmt_file: UploadFile | None,
    obo_file: UploadFile | None,
    annotation_file: UploadFile | None,
    mapper: GeneIdMapper,
    min_gene_set_size: int,
    max_gene_set_size: int,
    max_upload_bytes: int,
    max_terms: int,
    label: str,
    go_namespace: str,
) -> tuple[str, GeneIdMapping] | None:
    if gmt_file is not None and (obo_file is not None or annotation_file is not None):
        raise HTTPException(
            status_code=400,
            detail=f"{label}: upload either GMT or OBO plus annotations, not both",
        )
    if gmt_file is not None:
        text = await _read_upload_text(gmt_file, label=f"{label} GMT", max_bytes=max_upload_bytes)
        try:
            text, mapping = normalize_gmt_text(text, mapper)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"{label}: {exc}") from exc
        _validate_uploaded_gmt(
            text,
            known_genes=mapper.known_genes,
            min_gene_set_size=min_gene_set_size,
            max_gene_set_size=max_gene_set_size,
            max_terms=max_terms,
        )
        return text, mapping
    if obo_file is not None or annotation_file is not None:
        if obo_file is None or annotation_file is None:
            raise HTTPException(
                status_code=400,
                detail=f"{label}: GO/OBO uploads require both an OBO file and an annotation file",
            )
        obo_text = await _read_upload_text(
            obo_file, label=f"{label} OBO", max_bytes=max_upload_bytes
        )
        annotation_text = await _read_upload_text(
            annotation_file,
            label=f"{label} annotation file",
            max_bytes=max_upload_bytes,
        )
        try:
            text, mapping = go_obo_annotations_to_gmt_text(
                obo_text=obo_text,
                annotation_text=annotation_text,
                known_genes=mapper.known_genes,
                mapper=mapper,
                namespace=go_namespace,
            )
            validate_gmt_text(
                text,
                known_genes=mapper.known_genes,
                min_gene_set_size=min_gene_set_size,
                max_gene_set_size=max_gene_set_size,
                max_terms=max_terms,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"{label}: {exc}") from exc
        return text, mapping
    return None


async def _prepare_set_similarity(
    *,
    settings: AndesSettings,
    genes_file: UploadFile | None,
    query_gene_set_file: UploadFile | None,
    query_obo_file: UploadFile | None,
    query_annotation_file: UploadFile | None,
    gene_set_file: UploadFile | None,
    gene_set_obo_file: UploadFile | None,
    gene_set_annotation_file: UploadFile | None,
    genes_text: str | None,
    min_gene_set_size: int,
    max_gene_set_size: int,
    go_namespace: str,
) -> tuple[SetSimilarityRequest, dict[str, str], dict[str, str]]:
    mapper = _make_mapper(settings)
    files: dict[str, str] = {}
    path_fields: dict[str, str] = {}
    id_mapping: dict[str, object] = {}

    query_upload = await _normalize_gene_set_upload(
        gmt_file=query_gene_set_file,
        obo_file=query_obo_file,
        annotation_file=query_annotation_file,
        mapper=mapper,
        min_gene_set_size=min_gene_set_size,
        max_gene_set_size=max_gene_set_size,
        max_upload_bytes=settings.max_upload_bytes,
        max_terms=settings.max_terms_per_collection,
        label="query gene-set collection",
        go_namespace=go_namespace,
    )
    target_upload = await _normalize_gene_set_upload(
        gmt_file=gene_set_file,
        obo_file=gene_set_obo_file,
        annotation_file=gene_set_annotation_file,
        mapper=mapper,
        min_gene_set_size=min_gene_set_size,
        max_gene_set_size=max_gene_set_size,
        max_upload_bytes=settings.max_upload_bytes,
        max_terms=settings.max_terms_per_collection,
        label="target gene-set collection",
        go_namespace=go_namespace,
    )
    if query_upload is not None:
        query_gene_set_text, query_mapping = query_upload
        files["uploads/query_gene_sets.gmt"] = query_gene_set_text
        path_fields["query_gene_set_path"] = "uploads/query_gene_sets.gmt"
        id_mapping["query_collection"] = _mapping_payload(query_mapping)
    if target_upload is not None:
        target_gene_set_text, target_mapping = target_upload
        files["uploads/target_gene_sets.gmt"] = target_gene_set_text
        path_fields["gene_set_path"] = "uploads/target_gene_sets.gmt"
        id_mapping["target_collection"] = _mapping_payload(target_mapping)

    genes: list[str] | None = None
    if query_upload is None:
        text = genes_text or ""
        if genes_file is not None:
            text = await _read_upload_text(
                genes_file, label="genes file", max_bytes=settings.max_upload_bytes
            )
        try:
            submitted_genes = parse_gene_lines(text)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        mapping = mapper.map_many(submitted_genes)
        if not mapping.mapped:
            raise HTTPException(
                status_code=400,
                detail="none of the input genes are present in the embedding gene list",
            )
        genes = mapping.mapped
        id_mapping["genes"] = _mapping_payload(mapping, record_limit=None)

    request = SetSimilarityRequest(
        genes=genes,
        query_gene_set_path=(
            Path(path_fields["query_gene_set_path"])
            if "query_gene_set_path" in path_fields
            else None
        ),
        gene_set_path=(
            Path(path_fields["gene_set_path"]) if "gene_set_path" in path_fields else None
        ),
        min_gene_set_size=min_gene_set_size,
        max_gene_set_size=max_gene_set_size,
        id_mapping=id_mapping,
    )
    return request, files, path_fields


async def _prepare_gsea(
    *,
    settings: AndesSettings,
    ranked_file: UploadFile | None,
    gene_set_file: UploadFile | None,
    gene_set_obo_file: UploadFile | None,
    gene_set_annotation_file: UploadFile | None,
    ranked_text: str | None,
    min_gene_set_size: int,
    max_gene_set_size: int,
    go_namespace: str,
) -> tuple[GseaRequest, dict[str, str], dict[str, str]]:
    mapper = _make_mapper(settings)
    text = ranked_text or ""
    if ranked_file is not None:
        text = await _read_upload_text(
            ranked_file, label="ranked file", max_bytes=settings.max_upload_bytes
        )
    try:
        ranked_rows = parse_ranked_text(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    mapping = mapper.map_many([gene for gene, _score in ranked_rows])
    if not mapping.mapped:
        raise HTTPException(
            status_code=400,
            detail="none of the ranked genes are present in the embedding gene list",
        )
    score_by_submitted = {gene: score for gene, score in ranked_rows}
    ranked_genes = [
        (record.mapped, score_by_submitted[record.submitted])
        for record in mapping.records
        if record.mapped is not None
    ]

    files: dict[str, str] = {}
    path_fields: dict[str, str] = {}
    id_mapping: dict[str, object] = {"genes": _mapping_payload(mapping, record_limit=None)}
    gene_set_upload = await _normalize_gene_set_upload(
        gmt_file=gene_set_file,
        obo_file=gene_set_obo_file,
        annotation_file=gene_set_annotation_file,
        mapper=mapper,
        min_gene_set_size=min_gene_set_size,
        max_gene_set_size=max_gene_set_size,
        max_upload_bytes=settings.max_upload_bytes,
        max_terms=settings.max_terms_per_collection,
        label="gene-set collection",
        go_namespace=go_namespace,
    )
    if gene_set_upload is not None:
        gene_set_text, gene_set_mapping = gene_set_upload
        files["uploads/gene_sets.gmt"] = gene_set_text
        path_fields["gene_set_path"] = "uploads/gene_sets.gmt"
        id_mapping["target_collection"] = _mapping_payload(gene_set_mapping)

    request = GseaRequest(
        ranked_genes=ranked_genes,
        gene_set_path=(
            Path(path_fields["gene_set_path"]) if "gene_set_path" in path_fields else None
        ),
        min_gene_set_size=min_gene_set_size,
        max_gene_set_size=max_gene_set_size,
        id_mapping=id_mapping,
    )
    return request, files, path_fields


def _request_with_temp_paths(
    request,
    files: dict[str, str],
    path_fields: dict[str, str],
    tmpdir: str,
):
    data = request.model_dump(mode="json")
    root = Path(tmpdir)
    for relative_path, contents in files.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    for field, relative_path in path_fields.items():
        data[field] = str((root / relative_path).resolve())
    if isinstance(request, SetSimilarityRequest):
        return SetSimilarityRequest.model_validate(data)
    return GseaRequest.model_validate(data)


def _raise_if_preview_blocked(preview: dict[str, object]) -> None:
    if not preview.get("can_submit", False):
        raise HTTPException(
            status_code=400,
            detail={
                "message": "job exceeds server limits",
                "preview": preview,
            },
        )


def _owner_key(request: Request) -> str:
    user = request.headers.get("x-andes-user")
    if user and user.strip():
        return f"user:{user.strip()[:120]}"
    host = request.client.host if request.client else "unknown"
    return f"ip:{host}"


def _enforce_queue_limits(
    store: JobStore,
    settings: AndesSettings,
    *,
    owner_key: str,
) -> None:
    if settings.max_queued_jobs > 0 and store.queued_count() >= settings.max_queued_jobs:
        raise HTTPException(
            status_code=429,
            detail=(
                "server queue is full; "
                f"ANDES_MAX_QUEUED_JOBS={settings.max_queued_jobs}"
            ),
        )
    if (
        settings.max_jobs_per_owner > 0
        and store.active_count_for_owner(owner_key) >= settings.max_jobs_per_owner
    ):
        raise HTTPException(
            status_code=429,
            detail=(
                "too many queued/running jobs for this client; "
                f"ANDES_MAX_JOBS_PER_OWNER={settings.max_jobs_per_owner}"
            ),
        )


def create_app(settings: AndesSettings | None = None) -> FastAPI:
    settings = settings or get_settings()
    store = JobStore(settings.sqlite_path, settings.runs_dir)
    engine = AndesEngine(settings)
    app = FastAPI(title="ANDES App v2 API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_origin_regex=settings.cors_origin_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.store = store
    app.state.engine = engine

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/data/status")
    def data_status():
        checks = {
            "original_src": settings.original_src.exists(),
            "embedding_path": settings.embedding_path.exists(),
            "gene_list_path": settings.gene_list_path.exists(),
            "default_gene_set_path": settings.default_gene_set_path.exists(),
        }
        return {
            "ready": all(checks.values()),
            "checks": checks,
            "cache": _cache_status(settings.cache_dir),
            "jobs": store.storage_status(),
            "config": _config_status(settings),
        }

    @app.get("/admin/queue")
    def admin_queue(limit: int = 100):
        return {
            "stats": store.job_counts(),
            "limits": {
                "max_queued_jobs": settings.max_queued_jobs,
                "max_jobs_per_owner": settings.max_jobs_per_owner,
                "running_job_timeout_seconds": settings.running_job_timeout_seconds,
            },
            "jobs": store.queue_entries(limit=max(1, min(limit, 500))),
        }

    @app.post("/admin/queue/recover-stale")
    def recover_stale_jobs():
        result = store.recover_stale_running(
            timeout_seconds=settings.running_job_timeout_seconds
        )
        return result.__dict__

    @app.post("/preview/set-similarity")
    async def preview_set_similarity(
        genes_file: UploadFile | None = File(default=None),
        query_gene_set_file: UploadFile | None = File(default=None),
        query_obo_file: UploadFile | None = File(default=None),
        query_annotation_file: UploadFile | None = File(default=None),
        gene_set_file: UploadFile | None = File(default=None),
        gene_set_obo_file: UploadFile | None = File(default=None),
        gene_set_annotation_file: UploadFile | None = File(default=None),
        genes_text: str | None = Form(default=None),
        min_gene_set_size: int = Form(default=10),
        max_gene_set_size: int = Form(default=300),
        go_namespace: str = Form(default="biological_process"),
    ):
        _validate_size_range(min_gene_set_size, max_gene_set_size)
        request, files, path_fields = await _prepare_set_similarity(
            settings=settings,
            genes_file=genes_file,
            query_gene_set_file=query_gene_set_file,
            query_obo_file=query_obo_file,
            query_annotation_file=query_annotation_file,
            gene_set_file=gene_set_file,
            gene_set_obo_file=gene_set_obo_file,
            gene_set_annotation_file=gene_set_annotation_file,
            genes_text=genes_text,
            min_gene_set_size=min_gene_set_size,
            max_gene_set_size=max_gene_set_size,
            go_namespace=go_namespace,
        )
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                preview_request = _request_with_temp_paths(request, files, path_fields, tmpdir)
                return engine.preview_set_similarity(preview_request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/jobs/set-similarity", status_code=202)
    async def create_set_similarity_job(
        http_request: Request,
        genes_file: UploadFile | None = File(default=None),
        query_gene_set_file: UploadFile | None = File(default=None),
        query_obo_file: UploadFile | None = File(default=None),
        query_annotation_file: UploadFile | None = File(default=None),
        gene_set_file: UploadFile | None = File(default=None),
        gene_set_obo_file: UploadFile | None = File(default=None),
        gene_set_annotation_file: UploadFile | None = File(default=None),
        genes_text: str | None = Form(default=None),
        min_gene_set_size: int = Form(default=10),
        max_gene_set_size: int = Form(default=300),
        go_namespace: str = Form(default="biological_process"),
    ):
        _validate_size_range(min_gene_set_size, max_gene_set_size)
        request, files, path_fields = await _prepare_set_similarity(
            settings=settings,
            genes_file=genes_file,
            query_gene_set_file=query_gene_set_file,
            query_obo_file=query_obo_file,
            query_annotation_file=query_annotation_file,
            gene_set_file=gene_set_file,
            gene_set_obo_file=gene_set_obo_file,
            gene_set_annotation_file=gene_set_annotation_file,
            genes_text=genes_text,
            min_gene_set_size=min_gene_set_size,
            max_gene_set_size=max_gene_set_size,
            go_namespace=go_namespace,
        )
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                preview_request = _request_with_temp_paths(request, files, path_fields, tmpdir)
                preview = engine.preview_set_similarity(preview_request)
                _raise_if_preview_blocked(preview)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        owner_key = _owner_key(http_request)
        _enforce_queue_limits(store, settings, owner_key=owner_key)
        job = store.create_job(
            AnalysisKind.SET_SIMILARITY,
            request.model_dump(mode="json"),
            files=files,
            path_fields=path_fields,
            owner_key=owner_key,
        )
        return job

    @app.post("/preview/gsea")
    async def preview_gsea(
        ranked_file: UploadFile | None = File(default=None),
        gene_set_file: UploadFile | None = File(default=None),
        gene_set_obo_file: UploadFile | None = File(default=None),
        gene_set_annotation_file: UploadFile | None = File(default=None),
        ranked_text: str | None = Form(default=None),
        min_gene_set_size: int = Form(default=10),
        max_gene_set_size: int = Form(default=300),
        go_namespace: str = Form(default="biological_process"),
    ):
        _validate_size_range(min_gene_set_size, max_gene_set_size)
        request, files, path_fields = await _prepare_gsea(
            settings=settings,
            ranked_file=ranked_file,
            gene_set_file=gene_set_file,
            gene_set_obo_file=gene_set_obo_file,
            gene_set_annotation_file=gene_set_annotation_file,
            ranked_text=ranked_text,
            min_gene_set_size=min_gene_set_size,
            max_gene_set_size=max_gene_set_size,
            go_namespace=go_namespace,
        )
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                preview_request = _request_with_temp_paths(request, files, path_fields, tmpdir)
                return engine.preview_gsea(preview_request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/jobs/gsea", status_code=202)
    async def create_gsea_job(
        http_request: Request,
        ranked_file: UploadFile | None = File(default=None),
        gene_set_file: UploadFile | None = File(default=None),
        gene_set_obo_file: UploadFile | None = File(default=None),
        gene_set_annotation_file: UploadFile | None = File(default=None),
        ranked_text: str | None = Form(default=None),
        min_gene_set_size: int = Form(default=10),
        max_gene_set_size: int = Form(default=300),
        go_namespace: str = Form(default="biological_process"),
    ):
        _validate_size_range(min_gene_set_size, max_gene_set_size)
        request, files, path_fields = await _prepare_gsea(
            settings=settings,
            ranked_file=ranked_file,
            gene_set_file=gene_set_file,
            gene_set_obo_file=gene_set_obo_file,
            gene_set_annotation_file=gene_set_annotation_file,
            ranked_text=ranked_text,
            min_gene_set_size=min_gene_set_size,
            max_gene_set_size=max_gene_set_size,
            go_namespace=go_namespace,
        )
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                preview_request = _request_with_temp_paths(request, files, path_fields, tmpdir)
                preview = engine.preview_gsea(preview_request)
                _raise_if_preview_blocked(preview)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        owner_key = _owner_key(http_request)
        _enforce_queue_limits(store, settings, owner_key=owner_key)
        job = store.create_job(
            AnalysisKind.GSEA,
            request.model_dump(mode="json"),
            files=files,
            path_fields=path_fields,
            owner_key=owner_key,
        )
        return job

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str):
        job = store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        result = store.read_result(job_id)
        return {"job": job, "result": result, "queue": store.queue_status(job_id)}

    @app.post("/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        result = store.cancel_job(job_id)
        if result is None:
            raise HTTPException(status_code=404, detail="job not found")
        if not result.cancelled:
            raise HTTPException(
                status_code=409,
                detail=f"job is already {result.job.state.value} and cannot be cancelled",
            )
        return {"job": result.job, "queue": store.queue_status(job_id)}

    @app.get("/jobs/{job_id}/results")
    def get_results(job_id: str):
        job = store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        result = store.read_result(job_id)
        if result is None:
            raise HTTPException(status_code=404, detail="results not available")
        return result

    @app.get("/jobs/{job_id}/download/{filename}")
    def download_job_artifact(job_id: str, filename: str):
        job = store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        allowed = {"results.json", "results.csv", "pair-table.csv", "matrix.csv"}
        if filename not in allowed:
            raise HTTPException(status_code=404, detail="download not found")
        path = store.run_dir(job_id) / filename
        if filename != "results.json":
            path = store.run_dir(job_id) / "downloads" / filename
        if not path.exists():
            path = _materialize_download_from_result(store, job_id, filename) or path
        if not path.exists():
            raise HTTPException(status_code=404, detail="download not available")
        media_type = "application/json" if filename.endswith(".json") else "text/csv"
        return FileResponse(path, media_type=media_type, filename=filename)

    return app


app = create_app()
