from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from threading import RLock

from andes_core.config import AndesSettings
from andes_core.io import GeneIdMapper, file_provenance, gene_mapping_index_versions


class GeneMappingUnavailable(RuntimeError):
    """Raised when configured gene mapping cannot be initialized."""


@dataclass(frozen=True)
class GeneMappingServiceStatus:
    configured: bool
    ready: bool
    initialized: bool
    error: str | None
    manifest_hash: str | None
    cache_entries: int

    def as_dict(self) -> dict[str, object]:
        return {
            "configured": self.configured,
            "ready": self.ready,
            "initialized": self.initialized,
            "error": self.error,
            "manifest_hash": self.manifest_hash,
            "cache_entries": self.cache_entries,
        }


class GeneMappingService:
    def __init__(self, settings: AndesSettings):
        self._settings = settings
        self._lock = RLock()
        self._cache: dict[str, GeneIdMapper] = {}
        self._mapper: GeneIdMapper | None = None
        self._manifest_hash: str | None = None
        self._error: str | None = None
        self._initialized = False

    @property
    def configured(self) -> bool:
        return (
            self._settings.resolved_gene_mapping_path() is not None
            or self._settings.alias_path is not None
        )

    def initialize(self, *, force: bool = False) -> None:
        with self._lock:
            if self._initialized and not force:
                return
            self._load_locked()

    def get_mapper(self) -> GeneIdMapper:
        with self._lock:
            if not self._initialized:
                self._load_locked()
            if self._error is not None:
                self._load_locked()
            if self._error is not None:
                raise GeneMappingUnavailable(self._error)
            if self._mapper is None:
                self._load_locked()
            if self._error is not None:
                raise GeneMappingUnavailable(self._error)
            if self._mapper is None:  # pragma: no cover - defensive invariant
                raise GeneMappingUnavailable("gene mapping service did not initialize a mapper")
            return self._mapper

    def status(self) -> GeneMappingServiceStatus:
        with self._lock:
            return GeneMappingServiceStatus(
                configured=self.configured,
                ready=self._error is None and (self._mapper is not None or not self.configured),
                initialized=self._initialized,
                error=self._error,
                manifest_hash=self._manifest_hash,
                cache_entries=len(self._cache),
            )

    def _load_locked(self) -> None:
        try:
            manifest = self._manifest()
            manifest_key = _manifest_key(manifest)
            cached = self._cache.get(manifest_key)
            if cached is not None:
                self._mapper = cached
                self._manifest_hash = manifest_key
                self._error = None
                self._initialized = True
                return

            gene_mapping_path = self._settings.resolved_gene_mapping_path()
            gene_mapping_sqlite_path = self._settings.resolved_gene_mapping_sqlite_path()
            mapper = GeneIdMapper.from_paths(
                self._settings.gene_list_path,
                alias_path=self._settings.alias_path,
                gene_mapping_path=gene_mapping_path,
                gene_mapping_sqlite_path=gene_mapping_sqlite_path,
                species=self._settings.normalized_species(),
                canonical_id_namespace=self._settings.normalized_canonical_id_namespace(),
                min_mapping_overlap=self._settings.gene_mapping_min_overlap,
            )
        except (FileNotFoundError, OSError, ValueError, sqlite3.Error) as exc:
            self._mapper = None
            self._manifest_hash = None
            self._error = str(exc)
            self._initialized = True
            return

        self._cache[manifest_key] = mapper
        self._mapper = mapper
        self._manifest_hash = manifest_key
        self._error = None
        self._initialized = True

    def _manifest(self) -> dict[str, object]:
        gene_mapping_path = self._settings.resolved_gene_mapping_path()
        gene_mapping_sqlite_path = self._settings.resolved_gene_mapping_sqlite_path()
        return {
            "species": self._settings.normalized_species(),
            "canonical_id_namespace": self._settings.normalized_canonical_id_namespace(),
            "gene_mapping_min_overlap": self._settings.gene_mapping_min_overlap,
            "gene_list": file_provenance(self._settings.gene_list_path),
            "alias_file": file_provenance(self._settings.alias_path),
            "gene_mapping": file_provenance(gene_mapping_path),
            "gene_mapping_sqlite_file": (
                gene_mapping_sqlite_path.expanduser().resolve().name
                if gene_mapping_sqlite_path is not None
                else None
            ),
            "index": gene_mapping_index_versions(),
        }


def _manifest_key(manifest: dict[str, object]) -> str:
    payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
