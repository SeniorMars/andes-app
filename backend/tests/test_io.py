from __future__ import annotations

import pytest

from andes_core.io import (
    _load_embedding_cached,
    clean_gene_list,
    go_obo_annotations_to_gmt_text,
    load_embedding,
    parse_gene_lines,
    parse_obo_text,
    parse_ranked_text,
    validate_gmt_text,
)


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
