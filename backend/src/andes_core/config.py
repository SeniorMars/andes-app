from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AndesSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ANDES_", env_file=".env", extra="ignore")

    original_src: Path = Field(
        default=Path("/Users/charlie/Acdemica/ylab/ANDES/src"),
        description="Path to the optimized ANDES source directory.",
    )
    data_dir: Path = Field(default=Path("../data"), description="ANDES v2 data directory.")
    cache_dir: Path = Field(default=Path("../cache"), description="Null-cache directory.")
    runs_dir: Path = Field(default=Path("../runs"), description="Per-job run directory.")
    sqlite_path: Path = Field(default=Path("../runs/jobs.sqlite3"), description="Job DB path.")

    embedding_path: Path = Field(
        default=Path("/Users/charlie/Acdemica/ylab/ANDES/data/embedding/node2vec_consensus.csv")
    )
    gene_list_path: Path = Field(
        default=Path("/Users/charlie/Acdemica/ylab/ANDES/data/embedding/consensus_node.txt")
    )
    default_gene_set_path: Path = Field(
        default=Path(
            "/Users/charlie/Acdemica/ylab/ANDES/data/gene_sets/"
            "hsa_experimental_eval_BP_propagated.gmt"
        )
    )

    workers: int = 8
    job_concurrency: int = 1
    null_iterations: int = 1000
    seed: int | None = None
    query_memory_mb: float = 1024.0
    max_upload_bytes: int = 10_000_000
    max_term_pairs: int = 500_000
    max_terms_per_collection: int = 20_000
    allow_large_jobs: bool = False
    max_queued_jobs: int = 100
    max_jobs_per_owner: int = 10
    running_job_timeout_seconds: int = 21_600
    max_result_rows: int = 1000
    alias_path: Path | None = None
    cache_max_age_days: int = 30
    cache_min_keep_files: int = 8
    cache_max_bytes: int = 0
    job_max_age_days: int = 30
    job_min_keep: int = 20
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://localhost:3001",
            "http://0.0.0.0:3000",
            "http://0.0.0.0:3001",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:3001",
        ]
    )
    cors_origin_regex: str | None = Field(
        default=r"^https?://(localhost|0\.0\.0\.0|127(?:\.\d{1,3}){3}):\d+$"
    )


@lru_cache(maxsize=1)
def get_settings() -> AndesSettings:
    return AndesSettings()
