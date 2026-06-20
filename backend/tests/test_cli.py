from __future__ import annotations

import json

from typer.testing import CliRunner

from andes_core.cli import app


def test_validate_gene_mapping_command_reports_summary(tmp_path, monkeypatch):
    gene_list_path = tmp_path / "genes.txt"
    mapping_dir = tmp_path / "mappings"
    sqlite_path = tmp_path / "gene_mappings_hsa.sqlite3"
    mapping_dir.mkdir()
    gene_list_path.write_text("101\n102\n103\n", encoding="utf-8")
    (mapping_dir / "hsa_mapping_all.txt").write_text(
        "symbol\tentrez\tensembl\tuniprot_swiss\n"
        "ALPHA\t101\tENSG000001\tP00001\n"
        "BETA\t102\tENSG000002\tP00002\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANDES_GENE_LIST_PATH", str(gene_list_path))
    monkeypatch.setenv("ANDES_SPECIES", "hsa")
    monkeypatch.setenv("ANDES_GENE_MAPPING_DIR", str(mapping_dir))
    monkeypatch.setenv("ANDES_GENE_MAPPING_SQLITE_PATH", str(sqlite_path))
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["validate-gene-mapping"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["species"] == "hsa"
    assert payload["canonical_id_namespace"] == "entrez"
    assert payload["mapped_entrez_count"] == 2
    assert payload["missing_entrez_examples"] == ["103"]


def test_validate_data_builds_gene_mapping_index(tmp_path, monkeypatch):
    original_src = tmp_path / "andes-original" / "src"
    data_dir = tmp_path / "data"
    mapping_dir = tmp_path / "mappings"
    sqlite_path = tmp_path / "gene_mappings_hsa.sqlite3"
    original_src.mkdir(parents=True)
    data_dir.mkdir()
    mapping_dir.mkdir()
    embedding_path = data_dir / "embedding.csv"
    gene_list_path = data_dir / "genes.txt"
    gene_set_path = data_dir / "sets.gmt"
    embedding_path.write_text("1,0\n0,1\n", encoding="utf-8")
    gene_list_path.write_text("101\n102\n", encoding="utf-8")
    gene_set_path.write_text("TERM\tterm\t101\t102\n", encoding="utf-8")
    (mapping_dir / "hsa_mapping_all.txt").write_text(
        "symbol\tentrez\tensembl\tuniprot_swiss\n"
        "ALPHA\t101\tENSG00000100001\tP00001\n"
        "BETA\t102\tENSG00000100002\tP00002\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANDES_ORIGINAL_SRC", str(original_src))
    monkeypatch.setenv("ANDES_EMBEDDING_PATH", str(embedding_path))
    monkeypatch.setenv("ANDES_GENE_LIST_PATH", str(gene_list_path))
    monkeypatch.setenv("ANDES_DEFAULT_GENE_SET_PATH", str(gene_set_path))
    monkeypatch.setenv("ANDES_SPECIES", "hsa")
    monkeypatch.setenv("ANDES_GENE_MAPPING_DIR", str(mapping_dir))
    monkeypatch.setenv("ANDES_GENE_MAPPING_SQLITE_PATH", str(sqlite_path))
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["validate-data"])

    assert result.exit_code == 0
    assert "ANDES data paths are present." in result.output
    assert "Gene mapping index is ready." in result.output
    assert sqlite_path.exists()
