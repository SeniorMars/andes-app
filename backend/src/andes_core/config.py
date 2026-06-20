from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_SUPPORTED_SPECIES_CODES = {
    "cel",
    "danrer",
    "dme",
    "dre",
    "hsa",
    "mmu",
    "rno",
    "sce",
    "xla",
}


class AndesSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ANDES_",
        env_file=("../.env", ".env"),
        extra="ignore",
    )

    original_src: Path = Field(
        default=Path("../andes-original/src"),
        description="Path to the optimized ANDES source directory.",
    )
    original_adapter_module: str | None = Field(
        default=None,
        description=(
            "Optional importable adapter module that exposes load_data, "
            "func_optimized, and func_gsea from the pinned original ANDES code."
        ),
    )
    original_revision: str | None = Field(
        default=None,
        description=(
            "Expected original ANDES revision. When set, startup fails if the "
            "adapter or source checkout reports a different revision."
        ),
    )
    data_dir: Path = Field(default=Path("../data"), description="ANDES v2 data directory.")
    cache_dir: Path = Field(default=Path("../cache"), description="Null-cache directory.")
    runs_dir: Path = Field(default=Path("../runs"), description="Per-job run directory.")
    sqlite_path: Path = Field(default=Path("../runs/jobs.sqlite3"), description="Job DB path.")

    embedding_path: Path = Field(
        default=Path("../andes-original/data/embedding/node2vec_consensus.csv")
    )
    gene_list_path: Path = Field(
        default=Path("../andes-original/data/embedding/consensus_node.txt")
    )
    default_gene_set_path: Path = Field(
        default=Path(
            "../andes-original/data/gene_sets/"
            "hsa_experimental_eval_BP_propagated.gmt"
        )
    )

    workers: int = 8
    job_concurrency: int = 1
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_reload: bool = False
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
    preview_digest_secret: str | None = None
    preview_digest_ttl_seconds: int = Field(default=900, ge=1)
    token_hash_secret: str | None = None
    alias_path: Path | None = None
    species: str = "hsa"
    canonical_id_namespace: str = "entrez"
    gene_mapping_dir: Path | None = None
    gene_mapping_path: Path | None = None
    gene_mapping_sqlite_path: Path | None = None
    gene_mapping_min_overlap: float = Field(default=0.05, ge=0.0, le=1.0)
    cache_max_age_days: int = 30
    cache_min_keep_files: int = 8
    cache_max_bytes: int = 0
    job_max_age_days: int = 30
    job_min_keep: int = 20
    admin_token: str | None = None
    trusted_user_header: str | None = None
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

    def normalized_species(self) -> str:
        return self.species.strip().lower()

    def normalized_canonical_id_namespace(self) -> str:
        return self.canonical_id_namespace.strip().lower()

    def normalized_original_adapter_module(self) -> str | None:
        if self.original_adapter_module is None:
            return None
        normalized = self.original_adapter_module.strip()
        return normalized or None

    def normalized_original_revision(self) -> str | None:
        if self.original_revision is None:
            return None
        normalized = self.original_revision.strip()
        return normalized or None

    @field_validator("species")
    @classmethod
    def species_must_be_strict_code(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in _SUPPORTED_SPECIES_CODES:
            raise ValueError(
                "species must be one of: "
                + ", ".join(sorted(_SUPPORTED_SPECIES_CODES))
            )
        return normalized

    @field_validator("canonical_id_namespace")
    @classmethod
    def canonical_namespace_must_be_supported(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized != "entrez":
            raise ValueError("only canonical_id_namespace=entrez is currently supported")
        return normalized

    def resolved_gene_mapping_path(self) -> Path | None:
        if self.gene_mapping_path is not None:
            return self.gene_mapping_path
        if self.gene_mapping_dir is None:
            return None
        return self.gene_mapping_dir / f"{self.normalized_species()}_mapping_all.txt"

    def resolved_gene_mapping_sqlite_path(self) -> Path | None:
        if self.gene_mapping_sqlite_path is not None:
            return self.gene_mapping_sqlite_path
        if self.resolved_gene_mapping_path() is None:
            return None
        return self.cache_dir / f"gene_mappings_{self.normalized_species()}.sqlite3"


@lru_cache(maxsize=1)
def get_settings() -> AndesSettings:
    return AndesSettings()
