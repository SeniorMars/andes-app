from __future__ import annotations

from pathlib import Path

import pytest

from andes_core.config import AndesSettings
from andes_core.io import parse_ranked_text

ANDES_ROOT = Path("/Users/charlie/Acdemica/ylab/ANDES")
EMBEDDING = ANDES_ROOT / "data/embedding/node2vec_consensus.csv"
GENE_LIST = ANDES_ROOT / "data/embedding/consensus_node.txt"
GENE_SET = ANDES_ROOT / "data/gene_sets/hsa_experimental_eval_BP_propagated.gmt"
RANKED_LIST = ANDES_ROOT / "data/expression/GSE3467_rank.txt"

BENCHMARK_SET_EXAMPLE = [
    "10588",
    "84706",
    "137872",
    "56954",
    "6470",
    "4199",
    "56110",
    "2806",
    "2746",
    "2805",
    "2744",
    "122970",
]

BENCHMARK_GSEA_EXAMPLE = [
    ("7076", 22.390438445458),
    ("1001", 16.1489509439198),
    ("953", 15.553595860594),
]


DATA_AVAILABLE = all(path.exists() for path in [EMBEDDING, GENE_LIST, GENE_SET, RANKED_LIST])


@pytest.mark.skipif(not DATA_AVAILABLE, reason="original ANDES benchmark data not available")
def test_default_paths_follow_run_benchmarks_inputs():
    settings = AndesSettings()

    assert settings.embedding_path == EMBEDDING
    assert settings.gene_list_path == GENE_LIST
    assert settings.default_gene_set_path == GENE_SET
    assert settings.null_iterations == 1000
    assert settings.seed is None


@pytest.mark.skipif(not DATA_AVAILABLE, reason="original ANDES benchmark data not available")
def test_set_similarity_placeholder_genes_are_in_benchmark_gmt_and_embedding():
    embedding_genes = set(GENE_LIST.read_text(encoding="utf-8").splitlines())
    gmt_lines = GENE_SET.read_text(encoding="utf-8").splitlines()
    go_0043648 = next(line for line in gmt_lines if line.startswith("GO:0043648\t"))
    go_genes = set(go_0043648.split("\t")[2:])

    assert set(BENCHMARK_SET_EXAMPLE).issubset(embedding_genes)
    assert set(BENCHMARK_SET_EXAMPLE).issubset(go_genes)


@pytest.mark.skipif(not DATA_AVAILABLE, reason="original ANDES benchmark data not available")
def test_gsea_placeholder_rows_match_benchmark_ranked_list():
    top_rows = parse_ranked_text(
        "\n".join(RANKED_LIST.read_text(encoding="utf-8").splitlines()[:3])
    )

    assert top_rows == BENCHMARK_GSEA_EXAMPLE
