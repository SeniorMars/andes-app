from __future__ import annotations

from pathlib import Path

import pytest

from andes_core.io import GeneIdMapper, validate_gene_mapping_file

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAPPING_PATH = PROJECT_ROOT / "gene_mappings" / "output" / "current" / "hsa_mapping_all.txt"
GENE_LIST_PATH = PROJECT_ROOT / "andes-original" / "data" / "embedding" / "consensus_node.txt"

pytestmark = pytest.mark.skipif(
    not MAPPING_PATH.exists() or not GENE_LIST_PATH.exists(),
    reason="local hsa mapping and embedding gene list are not available",
)


def test_real_hsa_mapping_resolves_common_ids_and_reports_quality(tmp_path):
    sqlite_path = tmp_path / "gene_mappings_hsa.sqlite3"
    mapper = GeneIdMapper.from_paths(
        GENE_LIST_PATH,
        gene_mapping_path=MAPPING_PATH,
        gene_mapping_sqlite_path=sqlite_path,
        species="hsa",
    )

    mapping = mapper.map_many(
        [
            "TP53",
            "BRCA1",
            "EGFR",
            "ENSG00000146648.17",
            "P00533",
            "P40",
        ]
    )
    by_submitted = {record.submitted: record for record in mapping.records}

    assert by_submitted["TP53"].mapped == "7157"
    assert by_submitted["TP53"].source == "gene_mapping"
    assert by_submitted["BRCA1"].mapped == "672"
    assert by_submitted["EGFR"].mapped == "1956"
    assert by_submitted["ENSG00000146648.17"].mapped == "1956"
    assert by_submitted["P00533"].mapped == "1956"
    assert by_submitted["P40"].mapped is None
    assert by_submitted["P40"].source == "ambiguous"
    assert len(by_submitted["P40"].candidates) > 1
    assert mapping.source_counts["gene_mapping"] == 5
    assert mapping.source_counts["ambiguous"] == 1
    assert mapping.provenance is not None
    assert mapping.provenance["species"] == "hsa"
    assert mapping.provenance["canonical_id_namespace"] == "entrez"
    assert mapping.provenance["mapping_file"] == "hsa_mapping_all.txt"
    assert mapping.provenance["mapping_sha256"]
    assert mapping.provenance["selected_source_columns"] == [
        {"column": "symbol", "id_type": "symbol"},
        {"column": "entrez", "id_type": "entrez"},
        {"column": "ensembl", "id_type": "ensembl"},
        {"column": "uniprot_swiss", "id_type": "uniprot"},
        {"column": "hgnc_symbol", "id_type": "symbol"},
        {"column": "external_synonym", "id_type": "symbol"},
        {"column": "uniprotsptrembl", "id_type": "uniprot"},
    ]

    summary = validate_gene_mapping_file(
        mapping_path=MAPPING_PATH,
        gene_list_path=GENE_LIST_PATH,
        sqlite_path=sqlite_path,
        species="hsa",
    )
    assert summary["canonical_id_namespace"] == "entrez"
    assert summary["coverage"] > 0.9
    assert summary["selected_source_columns"] == mapping.provenance["selected_source_columns"]
    assert summary["alias_rows"] > 200_000
    assert summary["ambiguous_alias_count"] > 3_000
