from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator


class AnalysisKind(StrEnum):
    SET_SIMILARITY = "set_similarity"
    GSEA = "gsea"


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BaseAnalysisRequest(BaseModel):
    gene_set_path: Path | None = None
    embedding_path: Path | None = None
    gene_list_path: Path | None = None
    min_gene_set_size: int = Field(default=10, ge=1)
    max_gene_set_size: int = Field(default=300, ge=1)
    null_iterations: int | None = Field(default=None, ge=1)
    workers: int | None = Field(default=None, ge=1)
    seed: int | None = None
    id_mapping: dict[str, object] = Field(default_factory=dict)

    @field_validator("max_gene_set_size")
    @classmethod
    def max_must_be_positive(cls, value: int) -> int:
        return value


class SetSimilarityRequest(BaseAnalysisRequest):
    genes: list[str] | None = None
    query_gene_set_path: Path | None = None
    background_genes: list[str] | None = None

    @field_validator("genes", mode="before")
    @classmethod
    def clean_genes(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = [gene.strip() for gene in value if gene.strip()]
        return cleaned or None

    @model_validator(mode="after")
    def require_gene_list_or_query_collection(self) -> SetSimilarityRequest:
        if not self.genes and self.query_gene_set_path is None:
            raise ValueError("provide input genes or a query gene-set collection")
        return self


class GseaRequest(BaseAnalysisRequest):
    ranked_genes: list[tuple[str, float]]

    @field_validator("ranked_genes")
    @classmethod
    def require_ranked_genes(cls, value: list[tuple[str, float]]) -> list[tuple[str, float]]:
        if not value:
            raise ValueError("at least one ranked gene is required")
        return [(gene.strip(), float(score)) for gene, score in value if gene.strip()]


class ResultTerm(BaseModel):
    term: str
    description: str | None = None
    size: int | None = None
    query_term: str | None = None
    query_description: str | None = None
    query_size: int | None = None
    target_term: str | None = None
    target_description: str | None = None
    target_size: int | None = None
    true_score: float | None = None
    z_score: float
    p_value: float
    p_value_corrected: float
    log10_p_value_corrected: float
    significant: bool


class AnalysisResult(BaseModel):
    kind: AnalysisKind
    results: list[ResultTerm]
    input_gene_count: int
    valid_gene_count: int
    invalid_genes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    parameters: dict[str, object] = Field(default_factory=dict)


class JobRecord(BaseModel):
    id: str
    kind: AnalysisKind
    state: JobState
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    cancelled_at: str | None = None
    error: str | None = None
    owner_key: str | None = None
