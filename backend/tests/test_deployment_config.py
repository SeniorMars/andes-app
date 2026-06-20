from pathlib import Path

import pytest

from andes_core.config import AndesSettings

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_compose_passes_preview_digest_settings():
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert 'ANDES_PREVIEW_DIGEST_SECRET: "${ANDES_PREVIEW_DIGEST_SECRET:-}"' in compose
    assert (
        'ANDES_PREVIEW_DIGEST_TTL_SECONDS: "${ANDES_PREVIEW_DIGEST_TTL_SECONDS:-900}"'
        in compose
    )


def test_compose_passes_legacy_adapter_settings():
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert 'ANDES_ORIGINAL_ADAPTER_MODULE: "${ANDES_ORIGINAL_ADAPTER_MODULE:-}"' in compose
    assert 'ANDES_ORIGINAL_REVISION: "${ANDES_ORIGINAL_REVISION:-}"' in compose


def test_env_example_leaves_compose_original_root_blank():
    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

    assert "ANDES_ORIGINAL_ROOT=\n" in env_example
    assert "ANDES_ORIGINAL_ROOT=/path/to/ANDES" not in env_example


@pytest.mark.parametrize("species", ["hsa", "mmu", "dme", "danrer"])
def test_species_accepts_strict_codes(species):
    settings = AndesSettings(species=species, _env_file=None)

    assert settings.normalized_species() == species


@pytest.mark.parametrize("species", ["Hs", "h", "human", "hsa mapping", "../hsa"])
def test_species_rejects_unsafe_or_ambiguous_codes(species):
    with pytest.raises(ValueError, match="species"):
        AndesSettings(species=species, _env_file=None)


def test_canonical_namespace_is_explicit_and_entrez_only():
    settings = AndesSettings(canonical_id_namespace="entrez", _env_file=None)

    assert settings.normalized_canonical_id_namespace() == "entrez"
    with pytest.raises(ValueError, match="canonical_id_namespace"):
        AndesSettings(canonical_id_namespace="symbol", _env_file=None)
