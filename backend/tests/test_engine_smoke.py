from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

import pytest

from andes_core.config import AndesSettings
from andes_core.engine import AndesEngine
from andes_core.schemas import AnalysisKind, GseaRequest, SetSimilarityRequest

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"
ORIGINAL_SRC = Path(
    os.environ.get("ANDES_ORIGINAL_SRC", Path.home() / "Acdemica/ylab/ANDES/src")
)


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
    cache = cast(dict[str, Any], result.parameters["cache"])
    assert cache["kind"] == "es"
    assert cache["seed_strategy"] == "cache_key"
    assert cache["added_sizes"] >= 0
