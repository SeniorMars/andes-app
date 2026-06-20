from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from andes_core.config import AndesSettings
from andes_core.engine import AndesEngine, _sample_trace_indices
from andes_core.schemas import (
    AnalysisKind,
    AnalysisResult,
    GseaRequest,
    GseaResultParameters,
    SetSimilarityRequest,
)

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"
ORIGINAL_SRC = Path(
    os.environ.get("ANDES_ORIGINAL_SRC", Path.home() / "Acdemica/ylab/ANDES/src")
)


def test_sample_trace_indices_respects_max_points():
    indices = _sample_trace_indices(1000, 10, required_index=503)

    assert len(indices) <= 10
    assert indices[0] == 0
    assert indices[-1] == 999
    assert 503 in indices
    assert _sample_trace_indices(1000, 2, required_index=503) == [503, 999]


@pytest.mark.parametrize(
    ("ranked_emb", "expected_scores", "expected_running", "expected_es"),
    [
        (
            np.asarray([[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]], dtype=np.float32),
            [1.0, 0.0, -1.0],
            [1.0, 1.0, 0.0],
            1.0,
        ),
        (
            np.asarray([[-1.0, 0.0], [0.0, 1.0], [1.0, 0.0]], dtype=np.float32),
            [-1.0, 0.0, 1.0],
            [-1.0, -1.0, 0.0],
            -1.0,
        ),
        (
            np.asarray([[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]], dtype=np.float32),
            [1.0, 1.0, 1.0],
            [0.0, 0.0, 0.0],
            0.0,
        ),
        (
            np.asarray([[0.4, 0.0]], dtype=np.float32),
            [0.4],
            [0.0],
            0.0,
        ),
    ],
)
def test_gsea_trace_golden_matrix(ranked_emb, expected_scores, expected_running, expected_es):
    engine = AndesEngine.__new__(AndesEngine)
    engine.settings = AndesSettings(query_memory_mb=1024.0)
    e_unit = np.asarray([[1.0, 0.0]], dtype=np.float32)

    trace = engine._compute_gsea_es_trace(
        e_unit=e_unit,
        gene_set_idx=np.asarray([0], dtype=np.int32),
        ranked_emb=ranked_emb,
    )

    np.testing.assert_allclose(trace["best_match_score"], expected_scores, atol=1e-6)
    np.testing.assert_allclose(trace["running_es"], expected_running, atol=1e-6)
    assert trace["es"] == pytest.approx(expected_es, abs=1e-6)


def test_gsea_trace_blocked_memory_matches_full_matrix():
    ranked_emb = np.asarray(
        [[1.0, 0.0], [0.5, 0.5], [0.0, 1.0], [-1.0, 0.0]],
        dtype=np.float32,
    )
    e_unit = np.asarray(
        [[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]],
        dtype=np.float32,
    )
    gene_set_idx = np.asarray([0, 1, 2], dtype=np.int32)
    full_engine = AndesEngine.__new__(AndesEngine)
    full_engine.settings = AndesSettings(query_memory_mb=1024.0)
    blocked_engine = AndesEngine.__new__(AndesEngine)
    blocked_engine.settings = AndesSettings(query_memory_mb=0.000001)

    full_trace = full_engine._compute_gsea_es_trace(e_unit, gene_set_idx, ranked_emb)
    blocked_trace = blocked_engine._compute_gsea_es_trace(e_unit, gene_set_idx, ranked_emb)

    np.testing.assert_allclose(
        blocked_trace["best_match_score"], full_trace["best_match_score"], atol=1e-6
    )
    np.testing.assert_allclose(blocked_trace["running_es"], full_trace["running_es"], atol=1e-6)
    assert blocked_trace["es"] == pytest.approx(full_trace["es"], abs=1e-6)


def test_analysis_result_parameters_are_discriminated_by_mode():
    result = AnalysisResult(
        kind=AnalysisKind.GSEA,
        results=[],
        input_gene_count=1,
        valid_gene_count=1,
        invalid_genes=[],
        warnings=[],
        parameters={
            "mode": "ranked_enrichment",
            "min_gene_set_size": 1,
            "max_gene_set_size": 10,
            "null_iterations": 2,
            "workers": 1,
            "seed": 123,
            "seed_strategy": "fixed",
            "gene_set_path": "sets.gmt",
            "target_term_count": 1,
            "total_pairs": 1,
            "id_mapping": {},
            "analysis_provenance": {},
            "cache": {},
            "timing_seconds": {},
            "gsea_trace": None,
        },
    )

    assert isinstance(result.parameters, GseaResultParameters)
    assert result.parameters["mode"] == "ranked_enrichment"
    assert result.parameters.get("target_term_count") == 1


@pytest.mark.skipif(not ORIGINAL_SRC.exists(), reason="original ANDES source not available")
def test_set_similarity_smoke(tmp_path):
    settings = AndesSettings(
        original_src=ORIGINAL_SRC,
        embedding_path=FIXTURES / "mini_embedding.csv",
        gene_list_path=FIXTURES / "mini_genes.txt",
        default_gene_set_path=FIXTURES / "mini_gene_sets.gmt",
        cache_dir=tmp_path / "cache",
        null_iterations=2,
        workers=1,
    )
    result = AndesEngine(settings).run_set_similarity(
        SetSimilarityRequest(genes=["A", "B", "MISSING"], min_gene_set_size=1, null_iterations=2)
    )

    assert result.kind == AnalysisKind.SET_SIMILARITY
    assert result.valid_gene_count == 2
    assert result.invalid_genes == ["MISSING"]
    assert {row.term for row in result.results} == {"TERM_A", "TERM_B", "TERM_C"}
    cache = cast(dict[str, Any], result.parameters["cache"])
    timing = cast(dict[str, float], result.parameters["timing_seconds"])
    assert cache["kind"] == "bma"
    assert cache["seed_strategy"] == "cache_key"
    assert isinstance(cache["seed"], int)
    assert result.parameters["seed"] == cache["seed"]
    assert cache["added_size_pairs"] >= 0
    assert timing["cache"] >= 0
    assert timing["scoring"] >= 0
    provenance = cast(dict[str, Any], result.parameters["analysis_provenance"])
    assert provenance["species"] == "hsa"
    assert provenance["canonical_id_namespace"] == "entrez"
    assert cast(dict[str, Any], provenance["embedding"])["sha256"]
    assert cast(dict[str, Any], provenance["gene_set"])["sha256"]


@pytest.mark.skipif(not ORIGINAL_SRC.exists(), reason="original ANDES source not available")
def test_collection_similarity_smoke(tmp_path):
    settings = AndesSettings(
        original_src=ORIGINAL_SRC,
        embedding_path=FIXTURES / "mini_embedding.csv",
        gene_list_path=FIXTURES / "mini_genes.txt",
        default_gene_set_path=FIXTURES / "mini_gene_sets.gmt",
        cache_dir=tmp_path / "cache",
        null_iterations=2,
        workers=1,
    )
    result = AndesEngine(settings).run_set_similarity(
        SetSimilarityRequest(
            query_gene_set_path=FIXTURES / "mini_gene_sets.gmt",
            min_gene_set_size=1,
            null_iterations=2,
        )
    )

    assert result.kind == AnalysisKind.SET_SIMILARITY
    assert result.parameters["mode"] == "gene_set_collection"
    assert result.parameters["total_pairs"] == 9
    assert len(result.results) == 9
    assert all(row.query_term and row.target_term for row in result.results)
    cache = cast(dict[str, Any], result.parameters["cache"])
    assert cache["kind"] == "bma"
    assert cache["seed_strategy"] == "cache_key"
    assert cache["requested_size_pairs"] >= 1


@pytest.mark.skipif(not ORIGINAL_SRC.exists(), reason="original ANDES source not available")
def test_gsea_smoke(tmp_path):
    settings = AndesSettings(
        original_src=ORIGINAL_SRC,
        embedding_path=FIXTURES / "mini_embedding.csv",
        gene_list_path=FIXTURES / "mini_genes.txt",
        default_gene_set_path=FIXTURES / "mini_gene_sets.gmt",
        cache_dir=tmp_path / "cache",
        null_iterations=2,
        workers=1,
    )
    result = AndesEngine(settings).run_gsea(
        GseaRequest(
            ranked_genes=[("A", 3.0), ("B", 2.0), ("C", 1.0), ("MISSING", -1.0)],
            min_gene_set_size=1,
            null_iterations=2,
        )
    )

    assert result.kind == AnalysisKind.GSEA
    assert result.valid_gene_count == 3
    assert result.invalid_genes == ["MISSING"]
    assert {row.term for row in result.results} == {"TERM_A", "TERM_B", "TERM_C"}
    trace = cast(dict[str, Any], result.parameters["gsea_trace"])
    assert trace["algorithm"] == "andes_best_match_trace_v1"
    assert trace["exact"] is True
    assert trace["ranked_gene_count"] == 3
    assert trace["terms"]
    first_term = trace["terms"][0]
    row_by_term = {row.term: row.true_score for row in result.results}
    assert abs(first_term["es"] - row_by_term[first_term["term"]]) < 1e-5
    assert len(first_term["points"]) <= trace["max_points_per_term"]
    assert first_term["points"]
    assert {"rank", "gene", "match_score", "running_es"} <= set(first_term["points"][0])
    timing = cast(dict[str, float], result.parameters["timing_seconds"])
    assert timing["total"] >= timing["scoring"]
    assert timing["trace"] >= 0
    cache = cast(dict[str, Any], result.parameters["cache"])
    assert cache["kind"] == "es"
    assert cache["seed_strategy"] == "cache_key"
    assert cache["added_sizes"] >= 0
