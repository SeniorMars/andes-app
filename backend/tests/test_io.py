from __future__ import annotations

import hashlib
import os

import pytest

import andes_core.io as io_module
from andes_core.io import (
    GeneIdMapper,
    _load_embedding_cached,
    clean_gene_list,
    detect_gene_id_type,
    ensure_gene_mapping_sqlite,
    go_obo_annotations_to_gmt_text,
    load_embedding,
    normalize_gene_lookup_key,
    parse_gene_lines,
    parse_obo_text,
    parse_ranked_text,
    validate_gene_mapping_file,
    validate_gmt_text,
)
from andes_core.schemas import GseaRequest


def test_clean_gene_list_strips_empty_and_deduplicates():
    assert clean_gene_list([" A ", "", "B", "A", "C"]) == ["A", "B", "C"]


def test_parse_gene_lines_accepts_crlf():
    assert parse_gene_lines("A\r\nB\n\nC") == ["A", "B", "C"]


def test_parse_gene_lines_accepts_csv():
    assert parse_gene_lines("A,B\nC") == ["A", "B", "C"]


def test_parse_ranked_text_sorts_descending():
    assert parse_ranked_text("A\t1\nB\t3\nC\t2\n") == [("B", 3.0), ("C", 2.0), ("A", 1.0)]


def test_parse_ranked_text_accepts_csv():
    assert parse_ranked_text("A,1\nB,3\n") == [("B", 3.0), ("A", 1.0)]


def test_parse_ranked_text_rejects_bad_scores():
    with pytest.raises(ValueError, match="non-numeric"):
        parse_ranked_text("A\tnope\n")


@pytest.mark.parametrize("score", ["NaN", "inf", "-inf"])
def test_parse_ranked_text_rejects_non_finite_scores(score):
    with pytest.raises(ValueError, match="non-finite"):
        parse_ranked_text(f"A\t{score}\n")


def test_gsea_request_rejects_non_finite_scores():
    with pytest.raises(ValueError, match="finite"):
        GseaRequest(ranked_genes=[("A", float("nan"))])


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("7157", "entrez"),
        ("ENSG00000141510", "ensembl_gene"),
        ("ENSG00000141510.18", "ensembl_gene"),
        ("ENSMUSG00000059552.1", "ensembl_gene"),
        ("ENSDARG00000000001.2", "ensembl_gene"),
        ("ENSP00000269305", "ensembl_protein"),
        ("ENST00000269305", "ensembl_transcript"),
        ("P04637", "uniprot_like"),
        ("P04637-2", "uniprot_like"),
        ("A0A024RBG1", "uniprot_like"),
        ("ABC123", "symbol_like"),
        ("ENSGABC", "symbol_like"),
        ("P40", "symbol_like"),
    ],
)
def test_detect_gene_id_type_uses_stricter_patterns(value, expected):
    assert detect_gene_id_type(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("ENSMUSG00000059552.1", "ENSMUSG00000059552"),
        ("ENSDARG00000000001.2", "ENSDARG00000000001"),
        ("P04637-2", "P04637"),
    ],
)
def test_gene_lookup_normalization_handles_species_and_isoform_ids(value, expected):
    assert normalize_gene_lookup_key(value) == expected


def test_validate_gmt_text_reports_usable_terms():
    validation = validate_gmt_text(
        "TERM_A\talpha\tA\tB\nTERM_B\tbeta\tMISSING\n",
        known_genes={"A", "B", "C"},
        min_gene_set_size=1,
        max_gene_set_size=2,
    )

    assert validation.term_count == 2
    assert validation.usable_term_count == 1
    assert validation.gene_count == 3
    assert validation.matched_gene_count == 2


def test_validate_gmt_text_rejects_duplicate_terms():
    with pytest.raises(ValueError, match="duplicated"):
        validate_gmt_text(
            "TERM_A\talpha\tA\nTERM_A\talpha\tB\n",
            known_genes={"A", "B"},
            min_gene_set_size=1,
            max_gene_set_size=2,
        )


def test_parse_obo_text_parses_terms_and_parents():
    terms, _alt_ids = parse_obo_text(
        """
        [Term]
        id: GO:0000001
        name: root
        namespace: biological_process

        [Term]
        id: GO:0000002
        name: child
        namespace: biological_process
        is_a: GO:0000001 ! root
        """
    )

    assert terms["GO:0000002"].name == "child"
    assert terms["GO:0000002"].parents == ("GO:0000001",)


def test_go_obo_annotations_to_gmt_text_propagates_to_parents():
    gmt, mapping = go_obo_annotations_to_gmt_text(
        obo_text="""
        [Term]
        id: GO:0000001
        name: root
        namespace: biological_process

        [Term]
        id: GO:0000002
        name: child
        namespace: biological_process
        is_a: GO:0000001 ! root
        """,
        annotation_text="A\tGO:0000002\nB\tGO:0000002\n",
        known_genes={"A", "B"},
    )

    assert "GO:0000001\troot\tA\tB" in gmt
    assert "GO:0000002\tchild\tA\tB" in gmt
    assert mapping.mapped == ["A", "B"]


def test_gene_mapping_file_maps_supported_ids_to_entrez(tmp_path):
    gene_list_path = tmp_path / "genes.txt"
    mapping_path = tmp_path / "hsa_mapping_all.txt"
    sqlite_path = tmp_path / "gene_mappings.sqlite3"
    gene_list_path.write_text("101\n102\n", encoding="utf-8")
    mapping_path.write_text(
        "\t".join(
            [
                "symbol",
                "entrez",
                "ensembl",
                "uniprot_swiss",
                "hgnc_symbol",
                "external_synonym",
                "uniprotsptrembl",
            ]
        )
        + "\n"
        + "ALPHA\t101\tENSG000001.15\tP00001\tALPHA\tA1\tQ00001\n"
        + "BETA\t102\tENSG000002\tP00002\tBETA\tB1\tQ00002\n"
        + "GAMMA\t999\tENSG000999\tP00999\tGAMMA\tG1\tQ00999\n",
        encoding="utf-8",
    )

    mapper = GeneIdMapper.from_paths(
        gene_list_path,
        gene_mapping_path=mapping_path,
        gene_mapping_sqlite_path=sqlite_path,
    )
    mapping = mapper.map_many(["alpha", "ENSG000001.15", "Q00002", "101", "GAMMA"])

    assert mapping.mapped == ["101", "102"]
    assert mapping.unmapped == ["GAMMA"]
    assert [record.source for record in mapping.records] == [
        "gene_mapping",
        "gene_mapping",
        "gene_mapping",
        "direct_entrez",
        "unmapped",
    ]
    assert mapping.provenance is not None
    assert mapping.provenance["species"] == "unknown"
    assert mapping.provenance["canonical_id_namespace"] == "entrez"
    assert mapping.provenance["schema_version"] == "3"
    assert mapping.provenance["normalizer_version"]
    assert mapping.provenance["mapping_file"] == "hsa_mapping_all.txt"
    assert isinstance(mapping.provenance["mapping_sha256"], str)
    assert mapping.provenance["selected_source_columns"] == [
        {"column": "symbol", "id_type": "symbol"},
        {"column": "entrez", "id_type": "entrez"},
        {"column": "ensembl", "id_type": "ensembl"},
        {"column": "uniprot_swiss", "id_type": "uniprot"},
        {"column": "hgnc_symbol", "id_type": "symbol"},
        {"column": "external_synonym", "id_type": "symbol"},
        {"column": "uniprotsptrembl", "id_type": "uniprot"},
    ]
    assert mapping.provenance["ignored_source_columns"] == []
    assert mapping.source_counts["direct_entrez"] == 1
    assert mapping.source_counts["gene_mapping"] == 3
    assert mapping.source_counts["unmapped"] == 1
    assert sqlite_path.exists()


def test_gene_mapping_file_supports_hgnc_symbol_ensembl_and_entrez_columns(tmp_path):
    gene_list_path = tmp_path / "genes.txt"
    mapping_path = tmp_path / "hsa_mapping_all.txt"
    sqlite_path = tmp_path / "gene_mappings.sqlite3"
    gene_list_path.write_text("7157\n672\n", encoding="utf-8")
    mapping_path.write_text(
        "hgnc_symbol\tentrez\tensembl\n"
        "TP53\t7157\tENSG00000141510.18\n"
        "BRCA1\t672\tENSG00000012048\n",
        encoding="utf-8",
    )

    mapper = GeneIdMapper.from_paths(
        gene_list_path,
        gene_mapping_path=mapping_path,
        gene_mapping_sqlite_path=sqlite_path,
    )
    mapping = mapper.map_many(["TP53", "ENSG00000012048", "7157"])

    assert mapping.mapped == ["7157", "672"]
    assert mapping.unmapped == []
    assert [record.mapped for record in mapping.records] == ["7157", "672", "7157"]
    assert [record.source for record in mapping.records] == [
        "gene_mapping",
        "gene_mapping",
        "direct_entrez",
    ]
    assert mapping.provenance is not None
    assert mapping.provenance["selected_source_columns"] == [
        {"column": "hgnc_symbol", "id_type": "symbol"},
        {"column": "entrez", "id_type": "entrez"},
        {"column": "ensembl", "id_type": "ensembl"},
    ]


def test_gene_mapping_file_ignores_unknown_columns_and_prefers_entrez_header(tmp_path):
    gene_list_path = tmp_path / "genes.txt"
    mapping_path = tmp_path / "hsa_mapping_all.txt"
    sqlite_path = tmp_path / "gene_mappings.sqlite3"
    gene_list_path.write_text("101\n", encoding="utf-8")
    mapping_path.write_text(
        "gene_id\tentrez\tdescription\tstatus\tsymbol\n"
        "BAD_CANONICAL\t101\talpha kinase description\tactive\tALPHA\n",
        encoding="utf-8",
    )

    mapper = GeneIdMapper.from_paths(
        gene_list_path,
        gene_mapping_path=mapping_path,
        gene_mapping_sqlite_path=sqlite_path,
    )
    mapping = mapper.map_many(["ALPHA", "BAD_CANONICAL", "alpha kinase description", "active"])

    assert mapping.mapped == ["101"]
    assert [record.source for record in mapping.records] == [
        "gene_mapping",
        "unmapped",
        "unmapped",
        "unmapped",
    ]
    assert mapping.provenance is not None
    assert mapping.provenance["selected_source_columns"] == [
        {"column": "entrez", "id_type": "entrez"},
        {"column": "symbol", "id_type": "symbol"},
    ]
    assert mapping.provenance["ignored_source_columns"] == [
        "gene_id",
        "description",
        "status",
    ]


def test_curated_alias_file_overrides_shared_gene_mapping(tmp_path):
    gene_list_path = tmp_path / "genes.txt"
    alias_path = tmp_path / "aliases.tsv"
    mapping_path = tmp_path / "hsa_mapping_all.txt"
    sqlite_path = tmp_path / "gene_mappings.sqlite3"
    gene_list_path.write_text("101\n102\n", encoding="utf-8")
    alias_path.write_text("101\tALPHA\n", encoding="utf-8")
    mapping_path.write_text(
        "symbol\tentrez\tensembl\tuniprot_swiss\n"
        "ALPHA\t102\tENSG000002\tP00002\n",
        encoding="utf-8",
    )

    mapper = GeneIdMapper.from_paths(
        gene_list_path,
        alias_path=alias_path,
        gene_mapping_path=mapping_path,
        gene_mapping_sqlite_path=sqlite_path,
    )
    mapping = mapper.map_many(["ALPHA"])

    assert mapping.mapped == ["101"]
    assert mapping.records[0].source == "alias_file"
    assert mapping.provenance is not None
    assert mapping.provenance["alias_file"]["file"] == "aliases.tsv"
    assert mapping.provenance["alias_file"]["sha256"]


def test_gene_mapping_rejects_low_overlap_with_embedding_gene_list(tmp_path):
    gene_list_path = tmp_path / "genes.txt"
    mapping_path = tmp_path / "hsa_mapping_all.txt"
    sqlite_path = tmp_path / "gene_mappings.sqlite3"
    gene_list_path.write_text("101\n102\n103\n104\n", encoding="utf-8")
    mapping_path.write_text(
        "symbol\tentrez\tensembl\tuniprot_swiss\n"
        "ALPHA\t101\tENSG000001\tP00001\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="overlap"):
        GeneIdMapper.from_paths(
            gene_list_path,
            gene_mapping_path=mapping_path,
            gene_mapping_sqlite_path=sqlite_path,
            min_mapping_overlap=0.5,
        )


def test_gene_mapping_sqlite_invalidates_same_size_same_mtime_changes(tmp_path):
    gene_list_path = tmp_path / "genes.txt"
    mapping_path = tmp_path / "hsa_mapping_all.txt"
    sqlite_path = tmp_path / "gene_mappings.sqlite3"
    gene_list_path.write_text("101\n102\n", encoding="utf-8")
    first_mapping = (
        "symbol\tentrez\tensembl\tuniprot_swiss\n"
        "ALPHA\t101\tENSG000001\tP00001\n"
    )
    second_mapping = (
        "symbol\tentrez\tensembl\tuniprot_swiss\n"
        "ALPHA\t102\tENSG000002\tP00002\n"
    )
    assert len(first_mapping) == len(second_mapping)
    mapping_path.write_text(first_mapping, encoding="utf-8")
    fixed_time = 1_700_000_000
    os.utime(mapping_path, (fixed_time, fixed_time))

    first = GeneIdMapper.from_paths(
        gene_list_path,
        gene_mapping_path=mapping_path,
        gene_mapping_sqlite_path=sqlite_path,
    ).map_many(["ALPHA"])
    first_sha = first.provenance["mapping_sha256"] if first.provenance else None

    mapping_path.write_text(second_mapping, encoding="utf-8")
    os.utime(mapping_path, (fixed_time, fixed_time))
    second = GeneIdMapper.from_paths(
        gene_list_path,
        gene_mapping_path=mapping_path,
        gene_mapping_sqlite_path=sqlite_path,
    ).map_many(["ALPHA"])
    second_sha = second.provenance["mapping_sha256"] if second.provenance else None

    assert first.mapped == ["101"]
    assert second.mapped == ["102"]
    assert first_sha != second_sha


def test_gene_mapping_sqlite_invalidates_same_size_same_mtime_gene_list_changes(tmp_path):
    gene_list_path = tmp_path / "genes.txt"
    mapping_path = tmp_path / "hsa_mapping_all.txt"
    sqlite_path = tmp_path / "gene_mappings.sqlite3"
    mapping_path.write_text(
        "symbol\tentrez\tensembl\tuniprot_swiss\n"
        "ALPHA\t101\tENSG000001\tP00001\n"
        "BETA\t102\tENSG000002\tP00002\n",
        encoding="utf-8",
    )
    fixed_time = 1_700_000_000
    gene_list_path.write_text("101\n", encoding="utf-8")
    os.utime(gene_list_path, (fixed_time, fixed_time))

    first = GeneIdMapper.from_paths(
        gene_list_path,
        gene_mapping_path=mapping_path,
        gene_mapping_sqlite_path=sqlite_path,
    ).map_many(["ALPHA", "BETA"])
    first_gene_sha = first.provenance["gene_list_sha256"] if first.provenance else None

    gene_list_path.write_text("102\n", encoding="utf-8")
    os.utime(gene_list_path, (fixed_time, fixed_time))
    second = GeneIdMapper.from_paths(
        gene_list_path,
        gene_mapping_path=mapping_path,
        gene_mapping_sqlite_path=sqlite_path,
    ).map_many(["ALPHA", "BETA"])
    second_gene_sha = second.provenance["gene_list_sha256"] if second.provenance else None

    assert first.mapped == ["101"]
    assert second.mapped == ["102"]
    assert first_gene_sha != second_gene_sha


def test_gene_mapping_file_does_not_guess_ambiguous_aliases(tmp_path):
    gene_list_path = tmp_path / "genes.txt"
    mapping_path = tmp_path / "hsa_mapping_all.txt"
    sqlite_path = tmp_path / "gene_mappings.sqlite3"
    gene_list_path.write_text("101\n102\n", encoding="utf-8")
    mapping_path.write_text(
        "symbol\tentrez\tensembl\tuniprot_swiss\n"
        "SHARED\t101\tENSG000001\tP00001\n"
        "SHARED\t102\tENSG000002\tP00002\n",
        encoding="utf-8",
    )

    mapper = GeneIdMapper.from_paths(
        gene_list_path,
        gene_mapping_path=mapping_path,
        gene_mapping_sqlite_path=sqlite_path,
    )
    mapping = mapper.map_many(["SHARED", "ENSG000002"])

    assert mapping.mapped == ["102"]
    assert mapping.unmapped == ["SHARED"]
    assert mapping.records[0].source == "ambiguous"
    assert mapping.records[0].candidates == ("101", "102")
    assert mapping.records[1].source == "gene_mapping"
    assert mapping.source_counts["ambiguous"] == 1


def test_gene_mapping_map_many_uses_batch_sqlite_lookup(tmp_path, monkeypatch):
    gene_list_path = tmp_path / "genes.txt"
    mapping_path = tmp_path / "hsa_mapping_all.txt"
    sqlite_path = tmp_path / "gene_mappings.sqlite3"
    gene_list_path.write_text("101\n102\n", encoding="utf-8")
    mapping_path.write_text(
        "symbol\tentrez\tensembl\tuniprot_swiss\n"
        "ALPHA\t101\tENSG00000100001\tP00001\n"
        "BETA\t102\tENSG00000100002\tP00002\n",
        encoding="utf-8",
    )
    mapper = GeneIdMapper.from_paths(
        gene_list_path,
        gene_mapping_path=mapping_path,
        gene_mapping_sqlite_path=sqlite_path,
    )

    def fail_single_lookup(*_args, **_kwargs):
        raise AssertionError("per-gene lookup should not be used by map_many")

    monkeypatch.setattr(mapper, "_lookup_gene_mapping_db", fail_single_lookup)

    mapping = mapper.map_many(["ALPHA", "BETA", "MISSING"])

    assert mapping.mapped == ["101", "102"]
    assert mapping.unmapped == ["MISSING"]


def test_validate_gene_mapping_file_reports_quality_metrics(tmp_path):
    gene_list_path = tmp_path / "genes.txt"
    mapping_path = tmp_path / "hsa_mapping_all.txt"
    sqlite_path = tmp_path / "gene_mappings.sqlite3"
    gene_list_path.write_text("101\n102\n103\n", encoding="utf-8")
    mapping_path.write_text(
        "symbol\tentrez\tensembl\tuniprot_swiss\n"
        "ALPHA\t101\tENSG000001\tP00001\n"
        "SHARED\t101\tENSG000010\tP00010\n"
        "SHARED\t102\tENSG000020\tP00020\n",
        encoding="utf-8",
    )

    summary = validate_gene_mapping_file(
        mapping_path=mapping_path,
        sqlite_path=sqlite_path,
        gene_list_path=gene_list_path,
        species="hsa",
    )

    assert summary["species"] == "hsa"
    assert summary["canonical_id_namespace"] == "entrez"
    assert summary["embedding_gene_count"] == 3
    assert summary["mapped_entrez_count"] == 2
    assert summary["missing_entrez_count"] == 1
    assert summary["missing_entrez_examples"] == ["103"]
    assert summary["ambiguous_alias_count"] == 1
    assert summary["alias_rows_by_type"] == {
        "ensembl": 3,
        "entrez": 2,
        "symbol": 3,
        "uniprot": 3,
    }


def test_gene_mapping_sqlite_build_uses_hashed_snapshot(tmp_path, monkeypatch):
    gene_list_path = tmp_path / "genes.txt"
    mapping_path = tmp_path / "hsa_mapping_all.txt"
    sqlite_path = tmp_path / "gene_mappings.sqlite3"
    original_mapping = (
        "symbol\tentrez\tensembl\tuniprot_swiss\n"
        "ALPHA\t101\tENSG00000100001\tP00001\n"
    )
    gene_list_path.write_text("101\n102\n", encoding="utf-8")
    mapping_path.write_text(original_mapping, encoding="utf-8")
    expected_sha = hashlib.sha256(original_mapping.encode("utf-8")).hexdigest()
    original_loader = io_module._load_gene_mapping_rows

    def mutate_source_then_load(conn, snapshot_path, known_genes):
        mapping_path.write_text(
            "symbol\tentrez\tensembl\tuniprot_swiss\n"
            "ALPHA\t102\tENSG00000100002\tP00002\n",
            encoding="utf-8",
        )
        return original_loader(conn, snapshot_path, known_genes)

    monkeypatch.setattr(io_module, "_load_gene_mapping_rows", mutate_source_then_load)

    mapper = GeneIdMapper.from_paths(
        gene_list_path,
        gene_mapping_path=mapping_path,
        gene_mapping_sqlite_path=sqlite_path,
    )
    mapping = mapper.map_many(["ALPHA"])

    assert mapping.mapped == ["101"]
    assert mapping.provenance is not None
    assert mapping.provenance["mapping_sha256"] == expected_sha


def test_gene_mapping_sqlite_uses_gene_list_snapshot_for_filter_and_metadata(
    tmp_path,
    monkeypatch,
):
    gene_list_path = tmp_path / "genes.txt"
    mapping_path = tmp_path / "hsa_mapping_all.txt"
    sqlite_path = tmp_path / "gene_mappings.sqlite3"
    original_gene_list = "101\n"
    gene_list_path.write_text(original_gene_list, encoding="utf-8")
    mapping_path.write_text(
        "symbol\tentrez\tensembl\tuniprot_swiss\n"
        "ALPHA\t101\tENSG00000100001\tP00001\n"
        "BETA\t102\tENSG00000100002\tP00002\n",
        encoding="utf-8",
    )
    expected_gene_sha = hashlib.sha256(original_gene_list.encode("utf-8")).hexdigest()
    original_loader = io_module.load_gene_ids_with_fingerprint

    def load_snapshot_then_mutate(path, **kwargs):
        genes, fingerprint = original_loader(path, **kwargs)
        gene_list_path.write_text("102\n", encoding="utf-8")
        return genes, fingerprint

    monkeypatch.setattr(io_module, "load_gene_ids_with_fingerprint", load_snapshot_then_mutate)

    mapper = GeneIdMapper.from_paths(
        gene_list_path,
        gene_mapping_path=mapping_path,
        gene_mapping_sqlite_path=sqlite_path,
    )
    mapping = mapper.map_many(["ALPHA", "BETA"])

    assert mapping.mapped == ["101"]
    assert mapping.provenance is not None
    assert mapping.provenance["gene_list_sha256"] == expected_gene_sha


def test_gene_mapping_sqlite_build_uses_lock_and_cleans_failed_temp(tmp_path, monkeypatch):
    gene_list_path = tmp_path / "genes.txt"
    mapping_path = tmp_path / "hsa_mapping_all.txt"
    sqlite_path = tmp_path / "gene_mappings.sqlite3"
    gene_list_path.write_text("101\n", encoding="utf-8")
    mapping_path.write_text(
        "symbol\tentrez\tensembl\tuniprot_swiss\n"
        "ALPHA\t101\tENSG00000100001\tP00001\n",
        encoding="utf-8",
    )

    def fail_build(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(io_module, "_load_gene_mapping_rows", fail_build)

    with pytest.raises(RuntimeError, match="boom"):
        ensure_gene_mapping_sqlite(
            mapping_path=mapping_path,
            sqlite_path=sqlite_path,
            gene_list_path=gene_list_path,
            known_genes={"101"},
        )

    assert (tmp_path / "gene_mappings.sqlite3.lock").exists()
    assert not list(tmp_path.glob("gene_mappings.sqlite3.*.tmp"))


class CountingNormalizer:
    def __init__(self):
        self.calls = 0

    def l2_normalize_rows(self, raw):
        self.calls += 1
        return raw


def test_load_embedding_reuses_cached_normalized_matrix(tmp_path):
    embedding_path = tmp_path / "embedding.csv"
    gene_list_path = tmp_path / "genes.txt"
    embedding_path.write_text("1,0\n0,1\n", encoding="utf-8")
    gene_list_path.write_text("A\nB\n", encoding="utf-8")
    normalizer = CountingNormalizer()

    first_matrix, first_genes = load_embedding(embedding_path, gene_list_path, normalizer)
    second_matrix, second_genes = load_embedding(embedding_path, gene_list_path, normalizer)

    assert normalizer.calls == 1
    assert first_matrix is second_matrix
    assert first_matrix.flags.writeable is False
    assert first_genes == ["A", "B"]
    assert second_genes == ["A", "B"]
    assert first_genes is not second_genes
    with pytest.raises(ValueError):
        first_matrix[0, 0] = 99


def test_load_embedding_cache_keeps_at_most_two_matrices():
    assert _load_embedding_cached.cache_info().maxsize == 2


def test_load_embedding_cache_invalidates_when_source_metadata_changes(tmp_path):
    embedding_path = tmp_path / "embedding.csv"
    gene_list_path = tmp_path / "genes.txt"
    embedding_path.write_text("1,0\n0,1\n", encoding="utf-8")
    gene_list_path.write_text("A\nB\n", encoding="utf-8")
    normalizer = CountingNormalizer()

    load_embedding(embedding_path, gene_list_path, normalizer)
    embedding_path.write_text("1,0\n0,1\n1,1\n", encoding="utf-8")
    gene_list_path.write_text("A\nB\nC\n", encoding="utf-8")
    matrix, genes = load_embedding(embedding_path, gene_list_path, normalizer)

    assert normalizer.calls == 2
    assert matrix.shape == (3, 2)
    assert genes == ["A", "B", "C"]
