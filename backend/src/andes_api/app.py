from __future__ import annotations

import base64
import binascii
import csv
import hashlib
import hmac
import html
import io
import ipaddress
import json
import math
import secrets
import tempfile
import time
import zipfile
from collections.abc import Iterable
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from andes_core.config import AndesSettings, get_settings
from andes_core.engine import AndesEngine
from andes_core.gene_mapping import GeneMappingService, GeneMappingUnavailable
from andes_core.io import (
    GeneIdMapper,
    GeneIdMapping,
    GeneIdMapRecord,
    go_obo_annotations_to_gmt_text,
    normalize_gmt_text,
    parse_gene_lines,
    parse_ranked_text,
    validate_gmt_text,
)
from andes_core.schemas import AnalysisKind, GseaRequest, SetSimilarityRequest

from .storage import JobStore, generate_access_token

_PREVIEW_DIGEST_VERSION = "v2"
_PROCESS_LOCAL_PREVIEW_DIGEST_SECRET = secrets.token_bytes(32)
_REPORT_ZIP_MATRIX_CELL_LIMIT = 250_000
_MAX_ACCESS_TOKEN_CHARS = 512
_MAX_PREVIEW_DIGEST_CHARS = 512
_NO_STORE_HEADERS = {"Cache-Control": "no-store"}


def _set_no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _cache_status(cache_dir):
    root = cache_dir.expanduser().resolve()

    def summarize(name: str):
        directory = root / name
        files = sorted(directory.glob("*.pkl")) if directory.exists() else []
        total_bytes = sum(path.stat().st_size for path in files)
        newest = max((path.stat().st_mtime for path in files), default=None)
        return {
            "exists": directory.exists(),
            "files": len(files),
            "bytes": total_bytes,
            "newest_mtime": newest,
        }

    return {
        "exists": root.exists(),
        "bma": summarize("bma"),
        "es": summarize("es"),
    }


def _safe_path_filename(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return Path(value).name


def _public_path_key(key: str) -> str:
    if key == "path":
        return "file"
    return f"{key.removesuffix('_path')}_file"


def _private_roots(settings: AndesSettings) -> tuple[str, ...]:
    gene_mapping_path = settings.resolved_gene_mapping_path()
    gene_mapping_sqlite_path = settings.resolved_gene_mapping_sqlite_path()
    roots = [
        settings.runs_dir,
        settings.cache_dir,
        settings.original_src.parent,
        settings.embedding_path.parent,
        settings.gene_list_path.parent,
        settings.default_gene_set_path.parent,
    ]
    if settings.alias_path is not None:
        roots.append(settings.alias_path.parent)
    if gene_mapping_path is not None:
        roots.append(gene_mapping_path.parent)
    if gene_mapping_sqlite_path is not None:
        roots.append(gene_mapping_sqlite_path.parent)
    resolved: list[str] = []
    for root in roots:
        root_text = str(root.expanduser().resolve()).rstrip("/")
        if root_text and root_text != Path(root_text).anchor and root_text not in resolved:
            resolved.append(root_text)
    return tuple(sorted(resolved, key=len, reverse=True))


def _redact_private_roots(value: str, private_roots: tuple[str, ...]) -> str:
    redacted = value
    for root in private_roots:
        redacted = redacted.replace(root, "<server-path>")
    return redacted


def _public_payload(value: object, private_roots: tuple[str, ...]) -> object:
    if isinstance(value, dict):
        public: dict[str, object] = {}
        for key, nested in value.items():
            if key == "path" or key.endswith("_path"):
                filename = _safe_path_filename(nested)
                if filename:
                    public.setdefault(_public_path_key(key), filename)
                continue
            public[key] = _public_payload(nested, private_roots)
        return public
    if isinstance(value, list):
        return [_public_payload(item, private_roots) for item in value]
    if isinstance(value, str):
        return _redact_private_roots(value, private_roots)
    return value


def _public_result_payload(result: dict[str, Any], settings: AndesSettings) -> dict[str, Any]:
    return _public_payload(result, _private_roots(settings))  # type: ignore[return-value]


def _public_preview_payload(
    preview: dict[str, object],
    settings: AndesSettings,
) -> dict[str, object]:
    return _public_payload(preview, _private_roots(settings))  # type: ignore[return-value]


def _public_job_payload(job, settings: AndesSettings) -> dict[str, object]:
    payload = job.model_dump(mode="json")
    payload.pop("owner_key", None)
    return _public_payload(payload, _private_roots(settings))  # type: ignore[return-value]


def _path_fingerprint(path: Path | None) -> dict[str, object] | None:
    if path is None:
        return None
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        return {"path": str(resolved), "exists": False}
    stat = resolved.stat()
    return {
        "path": str(resolved),
        "exists": True,
        "mtime_ns": stat.st_mtime_ns,
        "size": stat.st_size,
    }


def _preview_settings_fingerprint(settings: AndesSettings) -> dict[str, object]:
    return {
        "embedding_path": _path_fingerprint(settings.embedding_path),
        "gene_list_path": _path_fingerprint(settings.gene_list_path),
        "default_gene_set_path": _path_fingerprint(settings.default_gene_set_path),
        "alias_path": _path_fingerprint(settings.alias_path),
        "original_src": _path_fingerprint(settings.original_src),
        "original_adapter_module": settings.normalized_original_adapter_module(),
        "original_revision": settings.normalized_original_revision(),
        "species": settings.normalized_species(),
        "canonical_id_namespace": settings.normalized_canonical_id_namespace(),
        "gene_mapping_path": _path_fingerprint(settings.resolved_gene_mapping_path()),
        "max_term_pairs": settings.max_term_pairs,
        "max_terms_per_collection": settings.max_terms_per_collection,
        "allow_large_jobs": settings.allow_large_jobs,
        "null_iterations": settings.null_iterations,
        "seed": settings.seed,
    }


def _preview_digest_payload(
    *,
    kind: AnalysisKind,
    request: SetSimilarityRequest | GseaRequest,
    files: dict[str, str],
    path_fields: dict[str, str],
    settings: AndesSettings,
) -> dict[str, object]:
    return {
        "kind": kind.value,
        "request": request.model_dump(mode="json"),
        "files": {
            path: hashlib.sha256(contents.encode("utf-8")).hexdigest()
            for path, contents in sorted(files.items())
        },
        "path_fields": dict(sorted(path_fields.items())),
        "settings": _preview_settings_fingerprint(settings),
    }


def _canonical_json_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _preview_digest_secret(settings: AndesSettings) -> bytes:
    if settings.preview_digest_secret:
        return settings.preview_digest_secret.encode("utf-8")
    return _PROCESS_LOCAL_PREVIEW_DIGEST_SECRET


def _canonical_json_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _base64url_encode(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _base64url_decode(payload: str) -> bytes:
    padding = "=" * (-len(payload) % 4)
    return base64.urlsafe_b64decode(f"{payload}{padding}")


def _sign_preview_digest(
    payload: dict[str, object],
    settings: AndesSettings,
    *,
    now: float | None = None,
) -> str:
    payload_hash = _canonical_json_hash(payload)
    issued_at = time.time() if now is None else now
    expires_at = datetime.fromtimestamp(
        issued_at + settings.preview_digest_ttl_seconds,
        UTC,
    )
    token_payload: dict[str, object] = {
        "payload_hash": payload_hash,
        "expires_at": expires_at.isoformat(),
    }
    encoded_payload = _base64url_encode(_canonical_json_bytes(token_payload))
    signature = hmac.new(
        _preview_digest_secret(settings),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{_PREVIEW_DIGEST_VERSION}.{encoded_payload}.{signature}"


def _preview_digest_matches(
    token: str | None,
    payload: dict[str, object],
    settings: AndesSettings,
    *,
    now: float | None = None,
) -> bool:
    if not token:
        return False
    if len(token) > _MAX_PREVIEW_DIGEST_CHARS:
        return False
    try:
        version, encoded_payload, signature = token.split(".", 2)
        token_payload = json.loads(_base64url_decode(encoded_payload))
    except (binascii.Error, ValueError, json.JSONDecodeError):
        return False
    if version != _PREVIEW_DIGEST_VERSION:
        return False
    if not isinstance(token_payload, dict):
        return False
    payload_hash = token_payload.get("payload_hash")
    expires_at = token_payload.get("expires_at")
    if not isinstance(payload_hash, str) or not isinstance(expires_at, str):
        return False
    try:
        expires_at_timestamp = datetime.fromisoformat(expires_at).timestamp()
    except ValueError:
        return False
    current_time = time.time() if now is None else now
    if current_time >= expires_at_timestamp:
        return False
    expected_hash = _canonical_json_hash(payload)
    if not hmac.compare_digest(payload_hash, expected_hash):
        return False
    expected_signature = hmac.new(
        _preview_digest_secret(settings),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature, expected_signature)


def _public_preview_with_digest(
    preview: dict[str, object],
    digest_payload: dict[str, object],
    settings: AndesSettings,
) -> dict[str, object]:
    public = _public_preview_payload(preview, settings)
    if preview.get("can_submit", False):
        public["preview_digest"] = _sign_preview_digest(digest_payload, settings)
    return public


def _config_status(settings: AndesSettings) -> dict[str, object]:
    return {
        "workers": settings.workers,
        "job_concurrency": settings.job_concurrency,
        "api_host": settings.api_host,
        "api_port": settings.api_port,
        "api_reload": settings.api_reload,
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
        "preview_digest_ttl_seconds": settings.preview_digest_ttl_seconds,
        "cache_max_age_days": settings.cache_max_age_days,
        "cache_min_keep_files": settings.cache_min_keep_files,
        "cache_max_bytes": settings.cache_max_bytes,
        "job_max_age_days": settings.job_max_age_days,
        "job_min_keep": settings.job_min_keep,
        "admin_token_configured": bool(settings.admin_token),
        "preview_digest_secret_configured": bool(settings.preview_digest_secret),
        "token_hash_secret_configured": bool(settings.token_hash_secret),
        "trusted_user_header": settings.trusted_user_header,
        "alias_file_configured": bool(settings.alias_path),
        "species": settings.normalized_species(),
        "canonical_id_namespace": settings.normalized_canonical_id_namespace(),
        "gene_mapping_min_overlap": settings.gene_mapping_min_overlap,
        "gene_mapping_dir_configured": bool(settings.gene_mapping_dir),
        "gene_mapping_file_configured": bool(settings.resolved_gene_mapping_path()),
    }


def _write_csv(path: Path, rows: list[list[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerows(_csv_safe_row(row) for row in rows)


def _csv_text(rows: Iterable[list[object]]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerows(_csv_safe_row(row) for row in rows)
    return output.getvalue()


def _write_zip_csv(
    archive: zipfile.ZipFile,
    name: str,
    rows: Iterable[list[object]],
) -> None:
    with archive.open(name, "w") as raw:
        with io.TextIOWrapper(raw, encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerows(_csv_safe_row(row) for row in rows)


def _csv_safe(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return value
    text = str(value)
    check = text.lstrip(" \t\r\n")
    if check.startswith(("=", "+", "-", "@")):
        return f"'{text}"
    return text


def _csv_safe_row(row: Iterable[object]) -> list[object]:
    return [_csv_safe(value) for value in row]


def _job_result_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = result.get("results")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _results_csv_rows(rows: list[dict[str, Any]]) -> list[list[object]]:
    return [
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
    ]


def _pair_table_rows(pair_rows: list[dict[str, Any]]) -> list[list[object]]:
    return [
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
    ]


def _matrix_rows(pair_rows: list[dict[str, Any]]) -> list[list[object]]:
    query_terms = list(dict.fromkeys(str(row["query_term"]) for row in pair_rows))
    target_terms = list(dict.fromkeys(str(row["target_term"]) for row in pair_rows))
    score_by_pair = {
        (str(row["query_term"]), str(row["target_term"])): row.get("z_score", "")
        for row in pair_rows
    }
    return [
        ["query_term", *target_terms],
        *[
            [
                query,
                *[score_by_pair.get((query, target), "") for target in target_terms],
            ]
            for query in query_terms
        ],
    ]


def _matrix_cell_count(pair_rows: list[dict[str, Any]]) -> int:
    query_terms = {str(row["query_term"]) for row in pair_rows}
    target_terms = {str(row["target_term"]) for row in pair_rows}
    return len(query_terms) * len(target_terms)


def _mapping_report_rows(result: dict[str, Any]) -> list[list[object]]:
    rows: list[list[object]] = [
        [
            "collection",
            "submitted_id",
            "mapped_id",
            "detected_type",
            "source",
            "status",
            "candidates",
        ]
    ]
    parameters = result.get("parameters")
    if not isinstance(parameters, dict):
        return rows
    id_mapping = parameters.get("id_mapping")
    if not isinstance(id_mapping, dict):
        return rows
    for collection, payload in sorted(id_mapping.items()):
        if not isinstance(payload, dict):
            continue
        records = payload.get("records")
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            mapped = record.get("mapped")
            source = record.get("source", "")
            candidates = record.get("candidates", [])
            if not isinstance(candidates, list | tuple):
                candidates = []
            rows.append(
                [
                    collection,
                    record.get("submitted", ""),
                    mapped or "",
                    record.get("id_type", ""),
                    source,
                    "mapped" if mapped else source if source == "ambiguous" else "unmapped",
                    "|".join(str(candidate) for candidate in candidates),
                ]
            )
    return rows


def _mapping_provenance_payload(result: dict[str, Any]) -> dict[str, object] | None:
    parameters = result.get("parameters")
    if not isinstance(parameters, dict):
        return None
    id_mapping = parameters.get("id_mapping")
    if not isinstance(id_mapping, dict):
        return None
    by_collection: dict[str, object] = {}
    for collection, payload in sorted(id_mapping.items()):
        if not isinstance(payload, dict):
            continue
        provenance = payload.get("mapping_provenance")
        if isinstance(provenance, dict):
            by_collection[collection] = provenance
    if not by_collection:
        return None
    unique = list(by_collection.values())
    return {
        "mapping_provenance": unique[0] if all(item == unique[0] for item in unique) else None,
        "collections": by_collection,
    }


def _materialize_download_from_result(
    store: JobStore,
    job_id: str,
    filename: str,
    settings: AndesSettings,
) -> Path | None:
    result = store.read_result(job_id)
    if result is None:
        return None
    public_result = _public_result_payload(result, settings)
    downloads = store.run_dir(job_id) / "downloads"
    path = downloads / filename
    if filename == "mapping-report.csv" and path.exists():
        return path
    if filename == "mapping-report.csv":
        mapping_rows = _mapping_report_rows(public_result)
        if len(mapping_rows) <= 1:
            return None
        _write_csv(path, mapping_rows)
        return path
    if filename == "mapping-provenance.json":
        provenance = _mapping_provenance_payload(public_result)
        if provenance is None:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(provenance, indent=2), encoding="utf-8")
        return path

    result_rows = _job_result_rows(public_result)
    if not result_rows:
        return None

    if filename == "results.csv":
        _write_csv(path, _results_csv_rows(result_rows))
        return path

    pair_rows = [row for row in result_rows if row.get("query_term") and row.get("target_term")]
    if not pair_rows:
        return None
    if filename == "pair-table.csv":
        _write_csv(path, _pair_table_rows(pair_rows))
        return path

    if filename == "matrix.csv":
        _write_csv(path, _matrix_rows(pair_rows))
        return path

    return None


def _float_value(row: dict[str, Any], key: str) -> float:
    value = row.get(key)
    return float(value) if isinstance(value, (int, float)) else 0.0


def _optional_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _svg_label(value: object) -> str:
    return html.escape(str(value), quote=True)


def _z_score_svg(rows: list[dict[str, Any]]) -> str | None:
    plotted = rows[:120]
    if len(plotted) < 2:
        return None
    width = 900
    height = 340
    pad = 48
    z_scores = [_float_value(row, "z_score") for row in plotted]
    min_z = min([*z_scores, 0.0])
    max_z = max([*z_scores, 0.0])

    def x_for(index: int) -> float:
        return pad + (index / max(1, len(plotted) - 1)) * (width - 2 * pad)

    def y_for(value: float) -> float:
        if max_z == min_z:
            return height / 2
        return height - pad - ((value - min_z) / (max_z - min_z)) * (height - 2 * pad)

    zero_y = y_for(0.0)
    circles = []
    for index, row in enumerate(plotted):
        z_score = _float_value(row, "z_score")
        term = row.get("term") or row.get("query_term") or f"row {index + 1}"
        fdr = row.get("p_value_corrected", "")
        fill = "#006f65" if row.get("significant") else "#3549a6"
        circles.append(
            "<circle "
            f"cx=\"{x_for(index):.2f}\" cy=\"{y_for(z_score):.2f}\" r=\"4\" "
            f"fill=\"{fill}\" opacity=\"0.82\">"
            f"<title>{_svg_label(term)}; z={z_score:.3f}; FDR={_svg_label(fdr)}</title>"
            "</circle>"
        )

    return (
        f"<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 {width} {height}\" "
        "role=\"img\" aria-label=\"Ranked Z-score plot\">"
        "<rect width=\"100%\" height=\"100%\" fill=\"#ffffff\"/>"
        f"<text x=\"{pad}\" y=\"28\" fill=\"#111917\" font-family=\"Arial\" "
        "font-size=\"18\" font-weight=\"700\">Ranked Z-score plot</text>"
        f"<text x=\"{pad}\" y=\"50\" fill=\"#5a6865\" font-family=\"Arial\" "
        f"font-size=\"12\">Top {len(plotted)} rows</text>"
        f"<line x1=\"{pad}\" x2=\"{width - pad}\" y1=\"{zero_y:.2f}\" y2=\"{zero_y:.2f}\" "
        "stroke=\"#aebfba\"/>"
        f"<line x1=\"{pad}\" x2=\"{pad}\" y1=\"{pad}\" y2=\"{height - pad}\" "
        "stroke=\"#aebfba\"/>"
        f"<line x1=\"{pad}\" x2=\"{width - pad}\" y1=\"{height - pad}\" "
        f"y2=\"{height - pad}\" stroke=\"#aebfba\"/>"
        f"<text x=\"{pad}\" y=\"{height - 14}\" fill=\"#5a6865\" "
        f"font-family=\"Arial\" font-size=\"12\">min z {min_z:.3f}; max z {max_z:.3f}</text>"
        f"{''.join(circles)}"
        "</svg>"
    )


def _pair_heatmap_svg(rows: list[dict[str, Any]]) -> str | None:
    pairs = [row for row in rows if row.get("query_term") and row.get("target_term")][:100]
    if not pairs:
        return None
    queries = list(dict.fromkeys(str(row["query_term"]) for row in pairs))[:12]
    targets = list(dict.fromkeys(str(row["target_term"]) for row in pairs))[:12]
    cell = 42
    left = 220
    top = 120
    width = left + len(targets) * cell + 80
    height = top + len(queries) * cell + 70
    max_abs = max([abs(_float_value(row, "z_score")) for row in pairs] + [1.0])
    pair_by_key = {
        (str(row["query_term"]), str(row["target_term"])): row for row in pairs
    }
    nodes = []
    for target_index, target in enumerate(targets):
        x = left + target_index * cell + cell / 2
        nodes.append(
            f"<text x=\"{x:.2f}\" y=\"{top - 18}\" transform=\"rotate(-35 {x:.2f} {top - 18})\" "
            "fill=\"#5a6865\" font-family=\"Arial\" font-size=\"11\" text-anchor=\"end\">"
            f"{_svg_label(target[:24])}</text>"
        )
    for query_index, query in enumerate(queries):
        y = top + query_index * cell + cell / 2 + 4
        nodes.append(
            f"<text x=\"{left - 12}\" y=\"{y:.2f}\" fill=\"#5a6865\" font-family=\"Arial\" "
            f"font-size=\"11\" text-anchor=\"end\">{_svg_label(query[:30])}</text>"
        )
        for target_index, target in enumerate(targets):
            row = pair_by_key.get((query, target))
            z_score = _float_value(row, "z_score") if row else 0.0
            opacity = 0.08 if row is None else 0.18 + min(abs(z_score) / max_abs, 1.0) * 0.72
            fill = "#3549a6" if z_score < 0 else "#006f65"
            x = left + target_index * cell
            y_rect = top + query_index * cell
            nodes.append(
                f"<rect x=\"{x}\" y=\"{y_rect}\" width=\"{cell - 4}\" height=\"{cell - 4}\" "
                f"fill=\"{fill}\" opacity=\"{opacity:.3f}\">"
                f"<title>{_svg_label(query)} vs {_svg_label(target)}; z={z_score:.3f}</title>"
                "</rect>"
            )
    return (
        f"<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 {width} {height}\" "
        "role=\"img\" aria-label=\"Term-pair heatmap\">"
        "<rect width=\"100%\" height=\"100%\" fill=\"#ffffff\"/>"
        "<text x=\"24\" y=\"32\" fill=\"#111917\" font-family=\"Arial\" "
        "font-size=\"18\" font-weight=\"700\">Term-pair heatmap</text>"
        "<text x=\"24\" y=\"54\" fill=\"#5a6865\" font-family=\"Arial\" font-size=\"12\">"
        "Color intensity scales by absolute Z-score.</text>"
        f"{''.join(nodes)}"
        "</svg>"
    )


def _gsea_trace_svg(result: dict[str, Any]) -> str | None:
    parameters = result.get("parameters")
    if not isinstance(parameters, dict):
        return None
    trace = parameters.get("gsea_trace")
    if not isinstance(trace, dict):
        return None
    terms = trace.get("terms")
    if not isinstance(terms, list) or not terms:
        return None
    term = terms[0]
    if not isinstance(term, dict):
        return None
    points = term.get("points")
    if not isinstance(points, list) or len(points) < 2:
        return None
    point_dicts = [point for point in points if isinstance(point, dict)]
    if len(point_dicts) < 2:
        return None

    parsed_points = []
    for point in point_dicts:
        rank = _optional_float(point.get("rank"))
        running_es = _optional_float(point.get("running_es"))
        match_score = _optional_float(point.get("match_score"))
        if rank is None or running_es is None or match_score is None:
            continue
        parsed_points.append((point, int(rank), running_es, match_score))
    if len(parsed_points) < 2:
        return None

    width = 900
    height = 380
    left = 58
    right = 28
    top = 58
    es_bottom = 250
    score_top = 280
    bottom = 340
    ranks = [rank for _point, rank, _running, _score in parsed_points]
    running = [value for _point, _rank, value, _score in parsed_points]
    match_scores = [score for _point, _rank, _running, score in parsed_points]
    min_rank = min(ranks)
    max_rank = max(ranks)
    min_es = min([*running, 0.0])
    max_es = max([*running, 0.0])
    min_match = min(match_scores)
    max_match = max(match_scores)

    def x_for(rank: int) -> float:
        if max_rank == min_rank:
            return left
        return left + ((rank - min_rank) / (max_rank - min_rank)) * (width - left - right)

    def es_y(value: float) -> float:
        if max_es == min_es:
            return (top + es_bottom) / 2
        return es_bottom - ((value - min_es) / (max_es - min_es)) * (es_bottom - top)

    def bar_y(value: float) -> float:
        if max_match == min_match:
            return (score_top + bottom) / 2
        return bottom - ((value - min_match) / (max_match - min_match)) * (bottom - score_top)

    path_points = " ".join(
        f"{x_for(rank):.2f},{es_y(value):.2f}" for rank, value in zip(ranks, running, strict=True)
    )
    zero_y = es_y(0.0)
    bars = []
    for point, rank, _running, score in parsed_points:
        x = x_for(rank)
        y = bar_y(score)
        gene = point.get("gene", "")
        best_gene = point.get("best_match_gene", "")
        bars.append(
            f"<line x1=\"{x:.2f}\" x2=\"{x:.2f}\" y1=\"{bottom}\" y2=\"{y:.2f}\" "
            "stroke=\"#006f65\" stroke-opacity=\"0.46\" stroke-width=\"1.3\">"
            f"<title>rank {rank}; {_svg_label(gene)} best matches {_svg_label(best_gene)}; "
            f"score={score:.3f}</title></line>"
        )

    title = term.get("description") or term.get("term") or "GSEA term"
    z_score = term.get("z_score", "")
    fdr = term.get("p_value_corrected", "")
    es_rank = term.get("es_rank", "")
    return (
        f"<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 {width} {height}\" "
        "role=\"img\" aria-label=\"GSEA running score plot\">"
        "<rect width=\"100%\" height=\"100%\" fill=\"#ffffff\"/>"
        f"<text x=\"{left}\" y=\"28\" fill=\"#111917\" font-family=\"Arial\" "
        "font-size=\"18\" font-weight=\"700\">GSEA running score</text>"
        f"<text x=\"{left}\" y=\"48\" fill=\"#5a6865\" font-family=\"Arial\" font-size=\"12\">"
        f"{_svg_label(title)}; z={_svg_label(z_score)}; FDR={_svg_label(fdr)}</text>"
        f"<line x1=\"{left}\" x2=\"{width - right}\" y1=\"{zero_y:.2f}\" y2=\"{zero_y:.2f}\" "
        "stroke=\"#cedbd8\"/>"
        f"<line x1=\"{left}\" x2=\"{left}\" y1=\"{top}\" y2=\"{bottom}\" stroke=\"#aebfba\"/>"
        f"<line x1=\"{left}\" x2=\"{width - right}\" y1=\"{es_bottom}\" y2=\"{es_bottom}\" "
        "stroke=\"#aebfba\"/>"
        f"<line x1=\"{left}\" x2=\"{width - right}\" y1=\"{bottom}\" y2=\"{bottom}\" "
        "stroke=\"#aebfba\"/>"
        f"{''.join(bars)}"
        f"<polyline points=\"{path_points}\" fill=\"none\" stroke=\"#3549a6\" "
        "stroke-width=\"2.6\" stroke-linejoin=\"round\" stroke-linecap=\"round\"/>"
        f"<text x=\"{left}\" y=\"{height - 14}\" fill=\"#5a6865\" font-family=\"Arial\" "
        f"font-size=\"12\">{len(point_dicts)} sampled ranks; ES peak rank "
        f"{_svg_label(es_rank)}</text>"
        "</svg>"
    )


def _write_report_zip(
    path: Path,
    job_id: str,
    result: dict[str, Any],
    settings: AndesSettings,
) -> Path:
    public_result = _public_result_payload(result, settings)
    rows = _job_result_rows(public_result)
    pair_rows = [row for row in rows if row.get("query_term") and row.get("target_term")]
    mapping_rows = _mapping_report_rows(public_result)
    existing_mapping_report = path.parent / "mapping-report.csv"
    mapping_provenance = _mapping_provenance_payload(public_result)
    parameters = public_result.get("parameters", {})
    cache = parameters.get("cache", {}) if isinstance(parameters, dict) else {}
    warnings = public_result.get("warnings", [])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("results.json", json.dumps(public_result, indent=2))
            if rows:
                _write_zip_csv(archive, "results.csv", _results_csv_rows(rows))
            if pair_rows:
                _write_zip_csv(archive, "pair-table.csv", _pair_table_rows(pair_rows))
                matrix_cells = _matrix_cell_count(pair_rows)
                if matrix_cells <= _REPORT_ZIP_MATRIX_CELL_LIMIT:
                    _write_zip_csv(archive, "matrix.csv", _matrix_rows(pair_rows))
                else:
                    archive.writestr(
                        "matrix-omitted.txt",
                        (
                            "matrix.csv was omitted because the dense query-by-target "
                            f"matrix would contain {matrix_cells} cells. Download "
                            "matrix.csv directly if you need the full matrix.\n"
                        ),
                    )
            if len(mapping_rows) > 1:
                _write_zip_csv(archive, "mapping-report.csv", mapping_rows)
            elif existing_mapping_report.exists():
                archive.write(existing_mapping_report, "mapping-report.csv")
            if mapping_provenance is not None:
                archive.writestr(
                    "mapping-provenance.json",
                    json.dumps(mapping_provenance, indent=2),
                )
            archive.writestr("parameters.json", json.dumps(parameters, indent=2))
            archive.writestr("cache.json", json.dumps(cache, indent=2))
            archive.writestr(
                "warnings.txt",
                "\n".join(str(warning) for warning in warnings)
                + ("\n" if isinstance(warnings, list) and warnings else ""),
            )
            z_svg = _z_score_svg(rows)
            if z_svg:
                archive.writestr("figures/z-scores.svg", z_svg)
            heatmap_svg = _pair_heatmap_svg(rows)
            if heatmap_svg:
                archive.writestr("figures/pair-heatmap.svg", heatmap_svg)
            gsea_svg = _gsea_trace_svg(public_result)
            if gsea_svg:
                archive.writestr("figures/gsea-running-score.svg", gsea_svg)
            archive.writestr(
                "README.txt",
                "\n".join(
                    [
                        f"ANDES report export for job {job_id}",
                        "",
                        "results.json contains the sanitized public result payload.",
                        "results.csv contains the result rows used by the web table.",
                        "mapping-report.csv lists submitted IDs, mapped IDs, detected type, "
                        "source, status, and candidates when mapping metadata is available.",
                        "mapping-provenance.json records the mapping file basename, mtime, "
                        "size, checksum, and species when available.",
                        "parameters.json and cache.json contain sanitized run metadata.",
                        "figures/*.svg contains server-generated SVG summaries when enough "
                        "result rows or GSEA trace data are available.",
                        "",
                    ]
                ),
            )
        tmp_path.replace(path)
    finally:
        tmp_path.unlink(missing_ok=True)
    return path


_UPLOAD_PATH_FIELDS = ("query_gene_set_path", "gene_set_path")


def _clone_uploaded_path_fields(
    store: JobStore,
    job_id: str,
    payload: dict[str, Any],
) -> tuple[dict[str, str], dict[str, str]]:
    source_run_dir = store.run_dir(job_id).resolve()
    files: dict[str, str] = {}
    path_fields: dict[str, str] = {}
    for field in _UPLOAD_PATH_FIELDS:
        value = payload.get(field)
        if not isinstance(value, str) or not value:
            continue
        path = Path(value)
        if not path.is_absolute():
            path = source_run_dir / path
        try:
            resolved = path.resolve(strict=True)
            relative = resolved.relative_to(source_run_dir)
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"source upload for {field} is no longer available",
            ) from exc
        except ValueError:
            continue
        relative_path = relative.as_posix()
        files[relative_path] = resolved.read_text(encoding="utf-8")
        path_fields[field] = relative_path
    if _payload_references_mapping_report(payload):
        mapping_report_path = source_run_dir / "downloads" / "mapping-report.csv"
        try:
            files["downloads/mapping-report.csv"] = mapping_report_path.read_text(
                encoding="utf-8"
            )
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=400,
                detail="source mapping report is no longer available",
            ) from exc
    return files, path_fields


def _request_from_payload(
    kind: AnalysisKind,
    payload: dict[str, Any],
) -> SetSimilarityRequest | GseaRequest:
    if kind == AnalysisKind.SET_SIMILARITY:
        return SetSimilarityRequest.model_validate(payload)
    return GseaRequest.model_validate(payload)


def _preview_existing_request(
    engine: AndesEngine,
    kind: AnalysisKind,
    request: SetSimilarityRequest | GseaRequest,
) -> dict[str, object]:
    if kind == AnalysisKind.SET_SIMILARITY:
        if not isinstance(request, SetSimilarityRequest):
            raise ValueError("stored set-similarity input is invalid")
        return engine.preview_set_similarity(request)
    if not isinstance(request, GseaRequest):
        raise ValueError("stored GSEA input is invalid")
    return engine.preview_gsea(request)


def _preview_set_similarity_with_temp_paths(
    engine: AndesEngine,
    request: SetSimilarityRequest,
    files: dict[str, str],
    path_fields: dict[str, str],
) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as tmpdir:
        preview_request = _request_with_temp_paths(request, files, path_fields, tmpdir)
        return engine.preview_set_similarity(preview_request)


def _preview_gsea_with_temp_paths(
    engine: AndesEngine,
    request: GseaRequest,
    files: dict[str, str],
    path_fields: dict[str, str],
) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as tmpdir:
        preview_request = _request_with_temp_paths(request, files, path_fields, tmpdir)
        return engine.preview_gsea(preview_request)


async def _read_upload_text(upload: UploadFile, *, label: str, max_bytes: int) -> str:
    contents = bytearray()
    while chunk := await upload.read(1024 * 1024):
        contents.extend(chunk)
        if len(contents) > max_bytes:
            raise HTTPException(
                status_code=400,
                detail=f"{label} is larger than {max_bytes} bytes",
            )
    try:
        return bytes(contents).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"{label} must be UTF-8 text") from exc


def _validate_size_range(min_gene_set_size: int, max_gene_set_size: int) -> None:
    if min_gene_set_size < 1:
        raise HTTPException(
            status_code=400,
            detail="min_gene_set_size must be >= 1",
        )
    if max_gene_set_size < 1:
        raise HTTPException(
            status_code=400,
            detail="max_gene_set_size must be >= 1",
        )
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
    include_records: bool = True,
) -> dict[str, object]:
    records = mapping.records if record_limit is None else mapping.records[:record_limit]
    source_counts = mapping.source_counts
    resolved_records = [record for record in mapping.records if record.mapped is not None]
    unique_canonical_ids = {record.mapped for record in resolved_records}
    unmapped_records = [record for record in mapping.records if record.source == "unmapped"]
    ambiguous_records = [record for record in mapping.records if record.source == "ambiguous"]
    payload: dict[str, object] = {
        "mapped_count": len(mapping.mapped),
        "submitted_record_count": len(mapping.records),
        "resolved_record_count": len(resolved_records),
        "unique_canonical_count": len(unique_canonical_ids),
        "unresolved_count": len(unmapped_records) + len(ambiguous_records),
        "unmapped_count": len(unmapped_records),
        "unmapped_examples": [record.submitted for record in unmapped_records][:10],
        "ambiguous_count": len(ambiguous_records),
        "ambiguous_examples": [record.submitted for record in ambiguous_records][:10],
        "id_type_counts": mapping.id_type_counts,
        "source_counts": source_counts,
        "mapping_provenance": mapping.provenance,
    }
    if include_records:
        payload["records"] = [asdict(record) for record in records]
    elif mapping.records:
        payload["mapping_report"] = "mapping-report.csv"
    return payload


def _mapping_report_artifact_files(id_mapping_with_records: dict[str, object]) -> dict[str, str]:
    rows = _mapping_report_rows({"parameters": {"id_mapping": id_mapping_with_records}})
    if len(rows) <= 1:
        return {}
    return {"downloads/mapping-report.csv": _csv_text(rows)}


def _payload_references_mapping_report(payload: dict[str, Any]) -> bool:
    id_mapping = payload.get("id_mapping")
    if not isinstance(id_mapping, dict):
        return False
    return any(
        isinstance(mapping_payload, dict)
        and mapping_payload.get("mapping_report") == "mapping-report.csv"
        for mapping_payload in id_mapping.values()
    )


def _map_ranked_rows_or_raise(
    mapper: GeneIdMapper,
    ranked_rows: list[tuple[str, float]],
) -> tuple[list[tuple[str, float]], GeneIdMapping]:
    records: list[GeneIdMapRecord] = []
    unmapped: list[str] = []
    submitted_scores: dict[str, list[float]] = {}
    canonical_groups: dict[str, list[dict[str, object]]] = {}

    records = mapper.map_records([submitted for submitted, _score in ranked_rows])
    for (_submitted, score), record in zip(ranked_rows, records, strict=True):
        submitted_scores.setdefault(record.submitted, []).append(float(score))
        if record.mapped is None:
            unmapped.append(record.submitted)
            continue
        canonical_groups.setdefault(record.mapped, []).append(
            {
                "submitted": record.submitted,
                "score": float(score),
                "source": record.source,
                "id_type": record.id_type,
            }
        )

    duplicate_submitted = [
        {"submitted": submitted, "scores": scores}
        for submitted, scores in submitted_scores.items()
        if len(scores) > 1
    ]
    canonical_collisions = [
        {"canonical": canonical, "submissions": submissions}
        for canonical, submissions in canonical_groups.items()
        if len(submissions) > 1
    ]
    if duplicate_submitted or canonical_collisions:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "ranked gene identifier collision",
                "duplicate_submitted": duplicate_submitted[:20],
                "canonical_collisions": canonical_collisions[:20],
                "policy": "submit one score per canonical Entrez gene",
            },
        )

    mapped: list[str] = []
    ranked_genes: list[tuple[str, float]] = []
    for (_submitted, score), record in zip(ranked_rows, records, strict=True):
        if record.mapped is None:
            continue
        mapped.append(record.mapped)
        ranked_genes.append((record.mapped, score))

    return (
        ranked_genes,
        GeneIdMapping(
            mapped=mapped,
            unmapped=unmapped,
            records=records,
            provenance=mapper.mapping_provenance,
        ),
    )


def _make_mapper(gene_mapping_service: GeneMappingService) -> GeneIdMapper:
    try:
        return gene_mapping_service.get_mapper()
    except GeneMappingUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail=f"gene mapping index unavailable: {exc}",
        ) from exc


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
            text, mapping = await run_in_threadpool(normalize_gmt_text, text, mapper)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"{label}: {exc}") from exc
        await run_in_threadpool(
            _validate_uploaded_gmt,
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
            text, mapping = await run_in_threadpool(
                go_obo_annotations_to_gmt_text,
                obo_text=obo_text,
                annotation_text=annotation_text,
                known_genes=mapper.known_genes,
                mapper=mapper,
                namespace=go_namespace,
            )
            await run_in_threadpool(
                validate_gmt_text,
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
    gene_mapping_service: GeneMappingService,
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
    include_mapping_artifact: bool = False,
) -> tuple[SetSimilarityRequest, dict[str, str], dict[str, str], dict[str, str]]:
    mapper = await run_in_threadpool(_make_mapper, gene_mapping_service)
    files: dict[str, str] = {}
    path_fields: dict[str, str] = {}
    id_mapping: dict[str, object] = {}
    id_mapping_with_records: dict[str, object] = {}

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
        id_mapping["query_collection"] = _mapping_payload(
            query_mapping,
            include_records=False,
        )
        id_mapping_with_records["query_collection"] = _mapping_payload(
            query_mapping,
            record_limit=None,
        )
    if target_upload is not None:
        target_gene_set_text, target_mapping = target_upload
        files["uploads/target_gene_sets.gmt"] = target_gene_set_text
        path_fields["gene_set_path"] = "uploads/target_gene_sets.gmt"
        id_mapping["target_collection"] = _mapping_payload(
            target_mapping,
            include_records=False,
        )
        id_mapping_with_records["target_collection"] = _mapping_payload(
            target_mapping,
            record_limit=None,
        )

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
        mapping = await run_in_threadpool(mapper.map_many, submitted_genes)
        if not mapping.mapped:
            raise HTTPException(
                status_code=400,
                detail="none of the input genes are present in the embedding gene list",
            )
        genes = mapping.mapped
        id_mapping["genes"] = _mapping_payload(mapping, include_records=False)
        id_mapping_with_records["genes"] = _mapping_payload(mapping, record_limit=None)

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
    artifact_files = (
        _mapping_report_artifact_files(id_mapping_with_records)
        if include_mapping_artifact
        else {}
    )
    return request, files, path_fields, artifact_files


async def _prepare_gsea(
    *,
    settings: AndesSettings,
    gene_mapping_service: GeneMappingService,
    ranked_file: UploadFile | None,
    gene_set_file: UploadFile | None,
    gene_set_obo_file: UploadFile | None,
    gene_set_annotation_file: UploadFile | None,
    ranked_text: str | None,
    min_gene_set_size: int,
    max_gene_set_size: int,
    go_namespace: str,
    include_mapping_artifact: bool = False,
) -> tuple[GseaRequest, dict[str, str], dict[str, str], dict[str, str]]:
    mapper = await run_in_threadpool(_make_mapper, gene_mapping_service)
    text = ranked_text or ""
    if ranked_file is not None:
        text = await _read_upload_text(
            ranked_file, label="ranked file", max_bytes=settings.max_upload_bytes
        )
    try:
        ranked_rows = parse_ranked_text(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ranked_genes, mapping = await run_in_threadpool(_map_ranked_rows_or_raise, mapper, ranked_rows)
    if not ranked_genes:
        raise HTTPException(
            status_code=400,
            detail="none of the ranked genes are present in the embedding gene list",
        )

    files: dict[str, str] = {}
    path_fields: dict[str, str] = {}
    id_mapping: dict[str, object] = {"genes": _mapping_payload(mapping, include_records=False)}
    id_mapping_with_records: dict[str, object] = {
        "genes": _mapping_payload(mapping, record_limit=None)
    }
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
        id_mapping["target_collection"] = _mapping_payload(
            gene_set_mapping,
            include_records=False,
        )
        id_mapping_with_records["target_collection"] = _mapping_payload(
            gene_set_mapping,
            record_limit=None,
        )

    request = GseaRequest(
        ranked_genes=ranked_genes,
        gene_set_path=(
            Path(path_fields["gene_set_path"]) if "gene_set_path" in path_fields else None
        ),
        min_gene_set_size=min_gene_set_size,
        max_gene_set_size=max_gene_set_size,
        id_mapping=id_mapping,
    )
    artifact_files = (
        _mapping_report_artifact_files(id_mapping_with_records)
        if include_mapping_artifact
        else {}
    )
    return request, files, path_fields, artifact_files


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


def _owner_key(request: Request, settings: AndesSettings) -> str:
    user = (
        request.headers.get(settings.trusted_user_header)
        if settings.trusted_user_header
        else None
    )
    if user and user.strip():
        return f"user:{user.strip()[:120]}"
    host = request.client.host if request.client else "unknown"
    return f"ip:{host}"


def _admin_token_from_request(request: Request) -> str | None:
    token = request.headers.get("x-andes-admin-token")
    if token:
        token = token.strip()
        if len(token) > _MAX_ACCESS_TOKEN_CHARS:
            return None
        return token
    authorization = request.headers.get("authorization", "")
    prefix = "bearer "
    if authorization.lower().startswith(prefix):
        token = authorization[len(prefix) :].strip()
        if len(token) > _MAX_ACCESS_TOKEN_CHARS:
            return None
        return token
    return None


def _is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    normalized = host.strip().lower().rstrip(".")
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _has_forwarded_headers(request: Request) -> bool:
    forwarded_headers = (
        "forwarded",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
        "x-real-ip",
    )
    return any(header in request.headers for header in forwarded_headers)


def _is_loopback_admin_request(request: Request) -> bool:
    client_host = request.client.host if request.client else None
    return (
        _is_loopback_host(client_host)
        and _is_loopback_host(request.url.hostname)
        and not _has_forwarded_headers(request)
    )


def _require_admin(request: Request, settings: AndesSettings) -> None:
    if settings.admin_token:
        token = _admin_token_from_request(request)
        if token and hmac.compare_digest(token, settings.admin_token):
            return
        raise HTTPException(status_code=403, detail="admin token required")
    if _is_loopback_admin_request(request):
        return
    raise HTTPException(status_code=403, detail="admin token required for remote admin access")


def _has_admin_access(request: Request, settings: AndesSettings) -> bool:
    if settings.admin_token:
        token = _admin_token_from_request(request)
        return bool(token and hmac.compare_digest(token, settings.admin_token))
    return _is_loopback_admin_request(request)


def _job_token_from_request(request: Request) -> str | None:
    token = request.headers.get("x-andes-job-token") or request.query_params.get("token")
    if token and token.strip():
        stripped = token.strip()
        if len(stripped) > _MAX_ACCESS_TOKEN_CHARS:
            raise HTTPException(status_code=403, detail="job token required")
        return stripped
    return None


def _require_job_access(
    request: Request,
    settings: AndesSettings,
    store: JobStore,
    job_id: str,
) -> None:
    token = _job_token_from_request(request)
    if token and store.verify_access_token(job_id, token):
        return
    if _has_admin_access(request, settings):
        return
    raise HTTPException(status_code=403, detail="job token required")


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
    store = JobStore(
        settings.sqlite_path,
        settings.runs_dir,
        token_hash_secret=settings.token_hash_secret,
    )
    engine: AndesEngine | None = None
    gene_mapping_service = GeneMappingService(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await run_in_threadpool(gene_mapping_service.initialize)
        yield

    app = FastAPI(title="ANDES App v2 API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_origin_regex=settings.cors_origin_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition"],
    )
    app.state.store = store
    app.state.engine = None
    app.state.gene_mapping_service = gene_mapping_service

    def get_engine() -> AndesEngine:
        nonlocal engine
        if engine is None:
            engine = AndesEngine(settings)
            app.state.engine = engine
        return engine

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/data/status")
    def data_status(http_request: Request):
        _require_admin(http_request, settings)
        gene_mapping_service.initialize()
        gene_mapping_path = settings.resolved_gene_mapping_path()
        checks = {
            "original_src": settings.original_src.exists(),
            "embedding_path": settings.embedding_path.exists(),
            "gene_list_path": settings.gene_list_path.exists(),
            "default_gene_set_path": settings.default_gene_set_path.exists(),
        }
        if gene_mapping_path is not None:
            checks["gene_mapping_path"] = gene_mapping_path.exists()
            mapping_status = gene_mapping_service.status()
            checks["gene_mapping_index"] = gene_mapping_path.exists() and mapping_status.ready
        else:
            mapping_status = gene_mapping_service.status()
        return {
            "ready": all(checks.values()),
            "checks": checks,
            "cache": _cache_status(settings.cache_dir),
            "jobs": store.storage_status(),
            "config": _config_status(settings),
            "gene_mapping": mapping_status.as_dict(),
        }

    @app.get("/admin/queue")
    def admin_queue(http_request: Request, limit: int = 100):
        _require_admin(http_request, settings)
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
    def recover_stale_jobs(http_request: Request):
        _require_admin(http_request, settings)
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
        request, files, path_fields, _artifact_files = await _prepare_set_similarity(
            settings=settings,
            gene_mapping_service=gene_mapping_service,
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
        digest_payload = _preview_digest_payload(
            kind=AnalysisKind.SET_SIMILARITY,
            request=request,
            files=files,
            path_fields=path_fields,
            settings=settings,
        )
        try:
            preview = await run_in_threadpool(
                _preview_set_similarity_with_temp_paths,
                get_engine(),
                request,
                files,
                path_fields,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _public_preview_with_digest(preview, digest_payload, settings)

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
        preview_digest: str | None = Form(default=None),
    ):
        _validate_size_range(min_gene_set_size, max_gene_set_size)
        request, files, path_fields, artifact_files = await _prepare_set_similarity(
            settings=settings,
            gene_mapping_service=gene_mapping_service,
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
            include_mapping_artifact=True,
        )
        digest_payload = _preview_digest_payload(
            kind=AnalysisKind.SET_SIMILARITY,
            request=request,
            files=files,
            path_fields=path_fields,
            settings=settings,
        )
        if not _preview_digest_matches(preview_digest, digest_payload, settings):
            try:
                preview = await run_in_threadpool(
                    _preview_set_similarity_with_temp_paths,
                    get_engine(),
                    request,
                    files,
                    path_fields,
                )
                _raise_if_preview_blocked(preview)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        owner_key = _owner_key(http_request, settings)
        _enforce_queue_limits(store, settings, owner_key=owner_key)
        access_token = generate_access_token()
        job = store.create_job(
            AnalysisKind.SET_SIMILARITY,
            request.model_dump(mode="json"),
            files={**files, **artifact_files},
            path_fields=path_fields,
            owner_key=owner_key,
            access_token=access_token,
        )
        return {**_public_job_payload(job, settings), "access_token": access_token}

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
        request, files, path_fields, _artifact_files = await _prepare_gsea(
            settings=settings,
            gene_mapping_service=gene_mapping_service,
            ranked_file=ranked_file,
            gene_set_file=gene_set_file,
            gene_set_obo_file=gene_set_obo_file,
            gene_set_annotation_file=gene_set_annotation_file,
            ranked_text=ranked_text,
            min_gene_set_size=min_gene_set_size,
            max_gene_set_size=max_gene_set_size,
            go_namespace=go_namespace,
        )
        digest_payload = _preview_digest_payload(
            kind=AnalysisKind.GSEA,
            request=request,
            files=files,
            path_fields=path_fields,
            settings=settings,
        )
        try:
            preview = await run_in_threadpool(
                _preview_gsea_with_temp_paths,
                get_engine(),
                request,
                files,
                path_fields,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _public_preview_with_digest(preview, digest_payload, settings)

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
        preview_digest: str | None = Form(default=None),
    ):
        _validate_size_range(min_gene_set_size, max_gene_set_size)
        request, files, path_fields, artifact_files = await _prepare_gsea(
            settings=settings,
            gene_mapping_service=gene_mapping_service,
            ranked_file=ranked_file,
            gene_set_file=gene_set_file,
            gene_set_obo_file=gene_set_obo_file,
            gene_set_annotation_file=gene_set_annotation_file,
            ranked_text=ranked_text,
            min_gene_set_size=min_gene_set_size,
            max_gene_set_size=max_gene_set_size,
            go_namespace=go_namespace,
            include_mapping_artifact=True,
        )
        digest_payload = _preview_digest_payload(
            kind=AnalysisKind.GSEA,
            request=request,
            files=files,
            path_fields=path_fields,
            settings=settings,
        )
        if not _preview_digest_matches(preview_digest, digest_payload, settings):
            try:
                preview = await run_in_threadpool(
                    _preview_gsea_with_temp_paths,
                    get_engine(),
                    request,
                    files,
                    path_fields,
                )
                _raise_if_preview_blocked(preview)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        owner_key = _owner_key(http_request, settings)
        _enforce_queue_limits(store, settings, owner_key=owner_key)
        access_token = generate_access_token()
        job = store.create_job(
            AnalysisKind.GSEA,
            request.model_dump(mode="json"),
            files={**files, **artifact_files},
            path_fields=path_fields,
            owner_key=owner_key,
            access_token=access_token,
        )
        return {**_public_job_payload(job, settings), "access_token": access_token}

    @app.get("/jobs/{job_id}")
    def get_job(http_request: Request, response: Response, job_id: str):
        job = store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        _require_job_access(http_request, settings, store, job_id)
        _set_no_store(response)
        result = store.read_result(job_id)
        return {
            "job": _public_job_payload(job, settings),
            "result": _public_result_payload(result, settings) if result is not None else None,
            "queue": store.queue_status(job_id),
        }

    @app.post("/jobs/{job_id}/rerun", status_code=202)
    def rerun_job(http_request: Request, response: Response, job_id: str):
        job = store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        _require_job_access(http_request, settings, store, job_id)
        _set_no_store(response)
        payload = store.read_input(job_id)
        files, path_fields = _clone_uploaded_path_fields(store, job_id, payload)
        try:
            request = _request_from_payload(job.kind, payload)
            with tempfile.TemporaryDirectory() as tmpdir:
                preview_request = _request_with_temp_paths(request, files, path_fields, tmpdir)
                preview = _preview_existing_request(get_engine(), job.kind, preview_request)
                _raise_if_preview_blocked(preview)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        owner_key = _owner_key(http_request, settings)
        _enforce_queue_limits(store, settings, owner_key=owner_key)
        access_token = generate_access_token()
        new_job = store.create_job(
            job.kind,
            request.model_dump(mode="json"),
            files=files,
            path_fields=path_fields,
            owner_key=owner_key,
            access_token=access_token,
        )
        return {**_public_job_payload(new_job, settings), "access_token": access_token}

    @app.post("/jobs/{job_id}/cancel")
    def cancel_job(http_request: Request, response: Response, job_id: str):
        job = store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        _require_job_access(http_request, settings, store, job_id)
        _set_no_store(response)
        result = store.cancel_job(job_id)
        if result is None:
            raise HTTPException(status_code=404, detail="job not found")
        if not result.cancelled:
            raise HTTPException(
                status_code=409,
                detail=f"job is already {result.job.state.value} and cannot be cancelled",
            )
        return {
            "job": _public_job_payload(result.job, settings),
            "queue": store.queue_status(job_id),
        }

    @app.get("/jobs/{job_id}/results")
    def get_results(http_request: Request, response: Response, job_id: str):
        job = store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        _require_job_access(http_request, settings, store, job_id)
        _set_no_store(response)
        result = store.read_result(job_id)
        if result is None:
            raise HTTPException(status_code=404, detail="results not available")
        return _public_result_payload(result, settings)

    @app.get("/jobs/{job_id}/download/{filename}")
    def download_job_artifact(http_request: Request, job_id: str, filename: str):
        job = store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        _require_job_access(http_request, settings, store, job_id)
        allowed = {
            "results.json",
            "results.csv",
            "pair-table.csv",
            "matrix.csv",
            "mapping-report.csv",
            "mapping-provenance.json",
            "report.zip",
        }
        if filename not in allowed:
            raise HTTPException(status_code=404, detail="download not found")
        if filename == "results.json":
            result = store.read_result(job_id)
            if result is None:
                raise HTTPException(status_code=404, detail="download not available")
            return JSONResponse(
                content=_public_result_payload(result, settings),
                headers={
                    **_NO_STORE_HEADERS,
                    "Content-Disposition": 'attachment; filename="results.json"',
                },
            )
        if filename == "mapping-provenance.json":
            result = store.read_result(job_id)
            if result is None:
                raise HTTPException(status_code=404, detail="download not available")
            provenance = _mapping_provenance_payload(_public_result_payload(result, settings))
            if provenance is None:
                raise HTTPException(status_code=404, detail="download not available")
            return JSONResponse(
                content=provenance,
                headers={
                    **_NO_STORE_HEADERS,
                    "Content-Disposition": 'attachment; filename="mapping-provenance.json"',
                },
            )
        if filename == "report.zip":
            result = store.read_result(job_id)
            if result is None:
                raise HTTPException(status_code=404, detail="download not available")
            path = store.run_dir(job_id) / "downloads" / "report.zip"
            path = _write_report_zip(path, job_id, result, settings)
            return FileResponse(
                path,
                media_type="application/zip",
                filename=f"{job_id}-report.zip",
                headers=_NO_STORE_HEADERS,
            )
        materialized_path = _materialize_download_from_result(store, job_id, filename, settings)
        if materialized_path is None or not materialized_path.exists():
            raise HTTPException(status_code=404, detail="download not available")
        return FileResponse(
            materialized_path,
            media_type="text/csv",
            filename=filename,
            headers=_NO_STORE_HEADERS,
        )

    return app


app = create_app()
