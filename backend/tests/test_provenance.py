from __future__ import annotations

from pathlib import Path

from andes_core.config import AndesSettings
from andes_core.provenance import analysis_provenance


class FakeLegacy:
    loaded_revision = "abc123"

    def provenance(self) -> dict[str, object]:
        return {
            "source_type": "adapter_module",
            "adapter_module": "andes_original_adapter",
            "expected_revision": "abc123",
            "loaded_revision": "abc123",
        }


def test_analysis_provenance_includes_legacy_adapter_pin(tmp_path):
    embedding = tmp_path / "embedding.csv"
    gene_list = tmp_path / "genes.txt"
    gene_set = tmp_path / "sets.gmt"
    for path in (embedding, gene_list, gene_set):
        path.write_text("fixture\n", encoding="utf-8")

    payload = analysis_provenance(
        paths={
            "embedding": embedding,
            "gene_list": gene_list,
            "gene_set": gene_set,
        },
        settings=AndesSettings(
            original_adapter_module="andes_original_adapter",
            original_revision="abc123",
            original_src=Path("/unused/source"),
            _env_file=None,
        ),
        legacy=FakeLegacy(),
    )

    assert payload["legacy_andes_revision"] == "abc123"
    assert payload["legacy_adapter"] == {
        "source_type": "adapter_module",
        "adapter_module": "andes_original_adapter",
        "expected_revision": "abc123",
        "loaded_revision": "abc123",
    }
