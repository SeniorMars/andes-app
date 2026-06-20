from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Protocol

from .config import AndesSettings
from .io import file_provenance


class LegacyModuleSet(Protocol):
    @property
    def loaded_revision(self) -> str | None: ...

    def provenance(self) -> dict[str, object]: ...


def git_revision(path: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(path.expanduser().resolve()), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    revision = result.stdout.strip()
    return revision or None


def analysis_provenance(
    *,
    paths: dict[str, Path],
    settings: AndesSettings,
    legacy: LegacyModuleSet | None = None,
) -> dict[str, object]:
    app_root = Path(__file__).resolve().parents[3]
    if legacy is not None:
        legacy_revision = legacy.loaded_revision
        legacy_payload = legacy.provenance()
    else:
        legacy_revision = git_revision(settings.original_src)
        legacy_payload = {
            "source_type": "source_dir",
            "source_dir": str(settings.original_src.expanduser().resolve()),
            "expected_revision": settings.normalized_original_revision(),
            "loaded_revision": legacy_revision,
        }
    payload: dict[str, object] = {
        "species": settings.normalized_species(),
        "canonical_id_namespace": settings.normalized_canonical_id_namespace(),
        "app_commit": git_revision(app_root),
        "legacy_andes_revision": legacy_revision,
        "legacy_adapter": legacy_payload,
        "original_src": str(settings.original_src.expanduser().resolve()),
        "embedding": file_provenance(paths.get("embedding")),
        "gene_list": file_provenance(paths.get("gene_list")),
        "gene_set": file_provenance(paths.get("gene_set")),
    }
    if "query_gene_set" in paths:
        payload["query_gene_set"] = file_provenance(paths.get("query_gene_set"))
    return payload
