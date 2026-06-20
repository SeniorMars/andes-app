from __future__ import annotations

import json
from pathlib import Path

import typer

from andes_api.storage import JobStore

from .cache import prune_cache
from .config import AndesSettings
from .engine import AndesEngine
from .gene_mapping import GeneMappingService
from .io import parse_gene_lines, parse_ranked_text, validate_gene_mapping_file
from .schemas import GseaRequest, SetSimilarityRequest

app = typer.Typer(help="ANDES v2 core CLI")


def _engine() -> AndesEngine:
    return AndesEngine(AndesSettings())


@app.command("validate-data")
def validate_data() -> None:
    settings = AndesSettings()
    gene_mapping_path = settings.resolved_gene_mapping_path()
    required = {
        "original_src": settings.original_src,
        "embedding_path": settings.embedding_path,
        "gene_list_path": settings.gene_list_path,
        "default_gene_set_path": settings.default_gene_set_path,
    }
    if gene_mapping_path is not None:
        required["gene_mapping_path"] = gene_mapping_path
    missing = [f"{name}: {path}" for name, path in required.items() if not Path(path).exists()]
    if missing:
        for item in missing:
            typer.echo(f"missing {item}", err=True)
        raise typer.Exit(1)
    gene_mapping_service = GeneMappingService(settings)
    gene_mapping_service.initialize(force=True)
    mapping_status = gene_mapping_service.status()
    if not mapping_status.ready:
        typer.echo(f"gene mapping index unavailable: {mapping_status.error}", err=True)
        raise typer.Exit(1)
    typer.echo("ANDES data paths are present.")
    if settings.resolved_gene_mapping_path() is not None:
        typer.echo("Gene mapping index is ready.")


@app.command("validate-gene-mapping")
def validate_gene_mapping() -> None:
    settings = AndesSettings()
    mapping_path = settings.resolved_gene_mapping_path()
    sqlite_path = settings.resolved_gene_mapping_sqlite_path()
    if mapping_path is None or sqlite_path is None:
        typer.echo(
            "gene mapping is not configured; set ANDES_GENE_MAPPING_DIR or "
            "ANDES_GENE_MAPPING_PATH",
            err=True,
        )
        raise typer.Exit(1)
    if not mapping_path.exists():
        typer.echo(f"missing gene_mapping_path: {mapping_path}", err=True)
        raise typer.Exit(1)
    result = validate_gene_mapping_file(
        mapping_path=mapping_path,
        sqlite_path=sqlite_path,
        gene_list_path=settings.gene_list_path,
        species=settings.normalized_species(),
        canonical_id_namespace=settings.normalized_canonical_id_namespace(),
    )
    typer.echo(json.dumps(result, indent=2))


@app.command("run-set")
def run_set_similarity(
    genes: Path = typer.Option(..., "--genes", help="Text file with one gene per line."),
    out: Path = typer.Option(..., "--out", help="JSON output path."),
    gene_set: Path | None = typer.Option(None, "--gene-set", help="GMT file."),
    workers: int = typer.Option(8, "--workers"),
    null_iterations: int = typer.Option(1000, "--ite"),
) -> None:
    request = SetSimilarityRequest(
        genes=parse_gene_lines(genes.read_text(encoding="utf-8")),
        gene_set_path=gene_set,
        workers=workers,
        null_iterations=null_iterations,
    )
    result = _engine().run_set_similarity(request)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    typer.echo(f"wrote {out}")


@app.command("run-gsea")
def run_gsea(
    ranked: Path = typer.Option(..., "--ranked", help="Tab/space-delimited gene score file."),
    out: Path = typer.Option(..., "--out", help="JSON output path."),
    gene_set: Path | None = typer.Option(None, "--gene-set", help="GMT file."),
    workers: int = typer.Option(8, "--workers"),
    null_iterations: int = typer.Option(1000, "--ite"),
) -> None:
    request = GseaRequest(
        ranked_genes=parse_ranked_text(ranked.read_text(encoding="utf-8")),
        gene_set_path=gene_set,
        workers=workers,
        null_iterations=null_iterations,
    )
    result = _engine().run_gsea(request)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    typer.echo(f"wrote {out}")


@app.command("show-config")
def show_config() -> None:
    typer.echo(json.dumps(AndesSettings().model_dump(mode="json"), indent=2))


@app.command("prune-cache")
def prune_cache_command(
    max_age_days: int | None = typer.Option(None, "--max-age-days"),
    min_keep_files: int | None = typer.Option(None, "--min-keep-files"),
    max_bytes: int | None = typer.Option(None, "--max-bytes"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    settings = AndesSettings()
    result = prune_cache(
        settings.cache_dir,
        max_age_days=max_age_days if max_age_days is not None else settings.cache_max_age_days,
        min_keep_files=(
            min_keep_files if min_keep_files is not None else settings.cache_min_keep_files
        ),
        max_bytes=max_bytes if max_bytes is not None else settings.cache_max_bytes,
        dry_run=dry_run,
    )
    typer.echo(json.dumps(result.__dict__, indent=2))


@app.command("prune-jobs")
def prune_jobs_command(
    max_age_days: int | None = typer.Option(None, "--max-age-days"),
    min_keep_jobs: int | None = typer.Option(None, "--min-keep-jobs"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    settings = AndesSettings()
    store = JobStore(
        settings.sqlite_path,
        settings.runs_dir,
        token_hash_secret=settings.token_hash_secret,
    )
    result = store.prune_finished_jobs(
        max_age_days=max_age_days if max_age_days is not None else settings.job_max_age_days,
        min_keep_jobs=min_keep_jobs if min_keep_jobs is not None else settings.job_min_keep,
        dry_run=dry_run,
    )
    typer.echo(json.dumps(result.__dict__, indent=2))


@app.command("cleanup")
def cleanup_command(
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    settings = AndesSettings()
    store = JobStore(
        settings.sqlite_path,
        settings.runs_dir,
        token_hash_secret=settings.token_hash_secret,
    )
    cache_result = prune_cache(
        settings.cache_dir,
        max_age_days=settings.cache_max_age_days,
        min_keep_files=settings.cache_min_keep_files,
        max_bytes=settings.cache_max_bytes,
        dry_run=dry_run,
    )
    job_result = store.prune_finished_jobs(
        max_age_days=settings.job_max_age_days,
        min_keep_jobs=settings.job_min_keep,
        dry_run=dry_run,
    )
    typer.echo(
        json.dumps(
            {
                "cache": cache_result.__dict__,
                "jobs": job_result.__dict__,
            },
            indent=2,
        )
    )
