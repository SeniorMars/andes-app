from __future__ import annotations

import sqlite3

import pytest

from andes_core.config import AndesSettings
from andes_core.gene_mapping import GeneMappingService, GeneMappingUnavailable
from andes_core.io import GeneIdMapper


def _settings(tmp_path, *, mapping_path=None) -> AndesSettings:
    gene_list_path = tmp_path / "genes.txt"
    gene_list_path.write_text("101\n102\n", encoding="utf-8")
    return AndesSettings(
        gene_list_path=gene_list_path,
        gene_mapping_path=mapping_path,
        gene_mapping_sqlite_path=tmp_path / "gene_mappings.sqlite3",
        gene_mapping_dir=None,
        alias_path=None,
        gene_mapping_min_overlap=0.0,
    )


def test_gene_mapping_service_reuses_cached_mapper_for_same_manifest(tmp_path):
    mapping_path = tmp_path / "hsa_mapping_all.txt"
    mapping_path.write_text(
        "symbol\tentrez\tensembl\tuniprot_swiss\n"
        "ALPHA\t101\tENSG00000100001\tP00001\n",
        encoding="utf-8",
    )
    service = GeneMappingService(_settings(tmp_path, mapping_path=mapping_path))

    service.initialize(force=True)
    first_mapper = service.get_mapper()
    service.initialize(force=True)
    second_mapper = service.get_mapper()

    assert first_mapper is second_mapper
    assert first_mapper.map_many(["ALPHA"]).mapped == ["101"]
    assert service.status().cache_entries == 1


def test_gene_mapping_service_changes_mapper_when_manifest_changes(tmp_path):
    mapping_path = tmp_path / "hsa_mapping_all.txt"
    mapping_path.write_text(
        "symbol\tentrez\tensembl\tuniprot_swiss\n"
        "ALPHA\t101\tENSG00000100001\tP00001\n",
        encoding="utf-8",
    )
    service = GeneMappingService(_settings(tmp_path, mapping_path=mapping_path))

    service.initialize(force=True)
    first_mapper = service.get_mapper()
    mapping_path.write_text(
        "symbol\tentrez\tensembl\tuniprot_swiss\n"
        "ALPHA\t102\tENSG00000100002\tP00002\n",
        encoding="utf-8",
    )
    service.initialize(force=True)
    second_mapper = service.get_mapper()

    assert first_mapper is not second_mapper
    assert second_mapper.map_many(["ALPHA"]).mapped == ["102"]
    assert service.status().cache_entries == 2


def test_gene_mapping_service_reports_unavailable_for_missing_mapping(tmp_path):
    missing_mapping_path = tmp_path / "missing_hsa_mapping_all.txt"
    service = GeneMappingService(_settings(tmp_path, mapping_path=missing_mapping_path))

    service.initialize(force=True)
    status = service.status()

    assert status.ready is False
    assert status.error is not None
    with pytest.raises(GeneMappingUnavailable):
        service.get_mapper()


def test_gene_mapping_service_reports_unavailable_for_sqlite_errors(tmp_path, monkeypatch):
    mapping_path = tmp_path / "hsa_mapping_all.txt"
    mapping_path.write_text(
        "symbol\tentrez\tensembl\tuniprot_swiss\n"
        "ALPHA\t101\tENSG00000100001\tP00001\n",
        encoding="utf-8",
    )

    def raise_sqlite_error(*_args, **_kwargs):
        raise sqlite3.DatabaseError("bad mapping index")

    monkeypatch.setattr(GeneIdMapper, "from_paths", raise_sqlite_error)
    service = GeneMappingService(_settings(tmp_path, mapping_path=mapping_path))

    service.initialize(force=True)
    status = service.status()

    assert status.ready is False
    assert status.error == "bad mapping index"
    with pytest.raises(GeneMappingUnavailable, match="bad mapping index"):
        service.get_mapper()


def test_gene_mapping_service_retries_after_initial_sqlite_error(tmp_path, monkeypatch):
    mapping_path = tmp_path / "hsa_mapping_all.txt"
    mapping_path.write_text(
        "symbol\tentrez\tensembl\tuniprot_swiss\n"
        "ALPHA\t101\tENSG00000100001\tP00001\n",
        encoding="utf-8",
    )
    original_from_paths = GeneIdMapper.from_paths
    calls = 0

    def flaky_from_paths(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.DatabaseError("temporary mapping index error")
        return original_from_paths(*args, **kwargs)

    monkeypatch.setattr(GeneIdMapper, "from_paths", flaky_from_paths)
    service = GeneMappingService(_settings(tmp_path, mapping_path=mapping_path))

    service.initialize(force=True)
    assert service.status().ready is False

    mapper = service.get_mapper()

    assert mapper.map_many(["ALPHA"]).mapped == ["101"]
    assert service.status().ready is True
