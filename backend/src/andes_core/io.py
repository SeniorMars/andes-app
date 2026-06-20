from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import sqlite3
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import RLock
from typing import Any, NamedTuple

import numpy as np

_ENSEMBL_ID_RE = re.compile(r"^ENS[A-Z]*([GPT])\d{11}(?:\.\d+)?$", re.IGNORECASE)
_UNIPROT_ID_RE = re.compile(
    r"^(?:[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})(?:-\d+)?$",
    re.IGNORECASE,
)

_GENE_MAPPING_ENTREZ_COLUMNS = {"entrez", "entrez_id"}
_GENE_MAPPING_SQLITE_SCHEMA_VERSION = "3"
_GENE_MAPPING_NORMALIZER_VERSION = "species_ensembl_uniprot_isoform_v2"
_GENE_MAPPING_COLUMN_TYPES = {
    "entrez": "entrez",
    "entrez_id": "entrez",
    "symbol": "symbol",
    "hgnc_symbol": "symbol",
    "external_synonym": "symbol",
    "alias": "symbol",
    "synonym": "symbol",
    "ensembl": "ensembl",
    "ensembl_gene": "ensembl",
    "ensembl_protein": "ensembl",
    "ensembl_transcript": "ensembl",
    "uniprot": "uniprot",
    "uniprot_swiss": "uniprot",
    "uniprot_trembl": "uniprot",
    "uniprotsptrembl": "uniprot",
}

fcntl_module: Any | None
try:
    import fcntl as fcntl_module
except ImportError:  # pragma: no cover - non-Unix fallback
    fcntl_module = None


def gene_mapping_index_versions() -> dict[str, str]:
    return {
        "schema_version": _GENE_MAPPING_SQLITE_SCHEMA_VERSION,
        "normalizer_version": _GENE_MAPPING_NORMALIZER_VERSION,
    }


@contextmanager
def _gene_mapping_sqlite_lock(sqlite_path: Path):
    resolved_sqlite_path = sqlite_path.expanduser().resolve()
    lock_path = resolved_sqlite_path.with_suffix(resolved_sqlite_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        if fcntl_module is not None:
            fcntl_module.flock(handle.fileno(), fcntl_module.LOCK_EX)
        try:
            yield
        finally:
            if fcntl_module is not None:
                fcntl_module.flock(handle.fileno(), fcntl_module.LOCK_UN)


@dataclass(frozen=True)
class GmtValidation:
    term_count: int
    usable_term_count: int
    gene_count: int
    matched_gene_count: int


@dataclass(frozen=True)
class GeneIdMapRecord:
    submitted: str
    mapped: str | None
    id_type: str
    source: str
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class GeneIdMapping:
    mapped: list[str]
    unmapped: list[str]
    records: list[GeneIdMapRecord]
    provenance: dict[str, object] | None = None

    @property
    def id_type_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in self.records:
            counts[record.id_type] = counts.get(record.id_type, 0) + 1
        return counts

    @property
    def source_counts(self) -> dict[str, int]:
        counts = {
            "direct_entrez": 0,
            "gene_mapping": 0,
            "alias_file": 0,
            "unmapped": 0,
            "ambiguous": 0,
        }
        for record in self.records:
            counts[record.source] = counts.get(record.source, 0) + 1
        return counts


class GeneIdMapper:
    def __init__(
        self,
        known_genes: set[str],
        alias_to_gene: dict[str, str] | None = None,
        gene_mapping_db_path: Path | None = None,
        mapping_provenance: dict[str, object] | None = None,
    ):
        self.known_genes = known_genes
        self.alias_to_gene = alias_to_gene or {}
        self.gene_mapping_db_path = gene_mapping_db_path
        self.mapping_provenance = mapping_provenance
        self._gene_mapping_conn: sqlite3.Connection | None = None
        self._gene_mapping_lock = RLock()

    @classmethod
    def from_paths(
        cls,
        gene_list_path: Path,
        alias_path: Path | None = None,
        gene_mapping_path: Path | None = None,
        gene_mapping_sqlite_path: Path | None = None,
        species: str = "unknown",
        canonical_id_namespace: str = "entrez",
        min_mapping_overlap: float = 0.0,
    ) -> GeneIdMapper:
        known_genes, gene_list_fingerprint = load_gene_ids_with_fingerprint(gene_list_path)
        alias_to_gene = load_alias_map(alias_path, known_genes) if alias_path else {}
        alias_provenance = file_provenance(alias_path) if alias_path else None
        gene_mapping_db_path = None
        mapping_provenance: dict[str, object] | None = (
            {
                "species": species,
                "canonical_id_namespace": canonical_id_namespace,
                "alias_file": alias_provenance,
            }
            if alias_provenance is not None
            else None
        )
        if gene_mapping_path is not None:
            gene_mapping_db_path = ensure_gene_mapping_sqlite(
                mapping_path=gene_mapping_path,
                sqlite_path=(
                    gene_mapping_sqlite_path
                    if gene_mapping_sqlite_path is not None
                    else gene_mapping_path.with_suffix(".sqlite3")
                ),
                gene_list_path=gene_list_path,
                known_genes=known_genes,
                gene_list_fingerprint=gene_list_fingerprint,
            )
            _validate_gene_mapping_overlap(
                sqlite_path=gene_mapping_db_path,
                known_genes=known_genes,
                min_overlap=min_mapping_overlap,
            )
            mapping_provenance = gene_mapping_provenance(
                sqlite_path=gene_mapping_db_path,
                species=species,
                canonical_id_namespace=canonical_id_namespace,
                alias_path=alias_path,
            )
        return cls(known_genes, alias_to_gene, gene_mapping_db_path, mapping_provenance)

    def _mapping_connection(self) -> sqlite3.Connection | None:
        if self.gene_mapping_db_path is None:
            return None
        if self._gene_mapping_conn is None:
            uri = f"file:{self.gene_mapping_db_path.expanduser().resolve()}?mode=ro"
            self._gene_mapping_conn = sqlite3.connect(
                uri,
                uri=True,
                check_same_thread=False,
            )
        return self._gene_mapping_conn

    def _lookup_gene_mapping_db(
        self,
        value: str,
        detected_id_type: str,
    ) -> GeneMappingLookup | None:
        del detected_id_type
        return self._lookup_gene_mapping_db_many([normalize_gene_lookup_key(value)]).get(
            normalize_gene_lookup_key(value)
        )

    def _lookup_gene_mapping_db_many(
        self,
        normalized_values: Iterable[str],
    ) -> dict[str, GeneMappingLookup]:
        query_values = sorted({value for value in normalized_values if value})
        if not query_values:
            return {}
        with self._gene_mapping_lock:
            conn = self._mapping_connection()
            if conn is None:
                return {}
            candidates_by_alias: dict[str, set[str]] = {value: set() for value in query_values}
            for chunk_start in range(0, len(query_values), 500):
                chunk = query_values[chunk_start : chunk_start + 500]
                placeholders = ",".join("?" for _value in chunk)
                rows = conn.execute(
                    f"""
                    SELECT normalized_alias, entrez
                    FROM aliases
                    WHERE normalized_alias IN ({placeholders})
                    """,
                    chunk,
                ).fetchall()
                for normalized_alias, entrez in rows:
                    candidates_by_alias.setdefault(str(normalized_alias), set()).add(str(entrez))
        lookups: dict[str, GeneMappingLookup] = {}
        for normalized_alias, candidates in candidates_by_alias.items():
            if not candidates:
                continue
            if len(candidates) == 1:
                lookups[normalized_alias] = GeneMappingLookup(
                    mapped=next(iter(candidates)),
                    candidates=(),
                )
            else:
                lookups[normalized_alias] = GeneMappingLookup(
                    mapped=None,
                    candidates=tuple(sorted(candidates, key=_gene_sort_key)),
                )
        return lookups

    def _map_without_gene_mapping_db(self, gene: str) -> tuple[GeneIdMapRecord | None, str]:
        value = str(gene).strip()
        id_type = detect_gene_id_type(value)
        normalized = normalize_gene_lookup_key(value)
        if value in self.known_genes:
            source = "direct_entrez" if id_type == "entrez" else "embedding"
            return GeneIdMapRecord(value, value, id_type, source), normalized
        mapped = self.alias_to_gene.get(value)
        if mapped is None:
            mapped = self.alias_to_gene.get(value.upper())
        if mapped is not None:
            return GeneIdMapRecord(value, mapped, id_type, "alias_file"), normalized
        return None, normalized

    def map_records(self, genes: Iterable[str]) -> list[GeneIdMapRecord]:
        values = [str(gene).strip() for gene in genes]
        records: list[GeneIdMapRecord | None] = []
        pending: dict[str, list[int]] = {}
        for value in values:
            record, normalized = self._map_without_gene_mapping_db(value)
            records.append(record)
            if record is None:
                pending.setdefault(normalized, []).append(len(records) - 1)
        lookups = self._lookup_gene_mapping_db_many(pending)
        for normalized, indices in pending.items():
            lookup = lookups.get(normalized)
            for index in indices:
                value = values[index]
                id_type = detect_gene_id_type(value)
                if lookup is None:
                    records[index] = GeneIdMapRecord(value, None, id_type, "unmapped")
                elif lookup.mapped is not None:
                    records[index] = GeneIdMapRecord(
                        value,
                        lookup.mapped,
                        id_type,
                        "gene_mapping",
                    )
                else:
                    records[index] = GeneIdMapRecord(
                        value,
                        None,
                        id_type,
                        "ambiguous",
                        lookup.candidates,
                    )
        return [record for record in records if record is not None]

    def map_one(self, gene: str) -> GeneIdMapRecord:
        records = self.map_records([gene])
        if records:
            return records[0]
        value = str(gene).strip()
        return GeneIdMapRecord(value, None, detect_gene_id_type(value), "unmapped")

    def map_many(self, genes: Iterable[str]) -> GeneIdMapping:
        seen: set[str] = set()
        mapped: list[str] = []
        unmapped: list[str] = []
        records = self.map_records(clean_gene_list(genes))
        for record in records:
            if record.mapped is None:
                unmapped.append(record.submitted)
                continue
            if record.mapped not in seen:
                seen.add(record.mapped)
                mapped.append(record.mapped)
        return GeneIdMapping(
            mapped=mapped,
            unmapped=unmapped,
            records=records,
            provenance=self.mapping_provenance,
        )


class GoTerm(NamedTuple):
    go_id: str
    name: str
    namespace: str
    parents: tuple[str, ...]


class GeneMappingLookup(NamedTuple):
    mapped: str | None
    candidates: tuple[str, ...]


def clean_gene_list(genes: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for gene in genes:
        value = str(gene).strip()
        if value and value not in seen:
            seen.add(value)
            cleaned.append(value)
    return cleaned


def detect_gene_id_type(value: str) -> str:
    gene = value.strip()
    if not gene:
        return "empty"
    upper = gene.upper()
    if gene.isdigit():
        return "entrez"
    ensembl_match = _ENSEMBL_ID_RE.match(gene)
    if ensembl_match and ensembl_match.group(1).upper() == "G":
        return "ensembl_gene"
    if ensembl_match and ensembl_match.group(1).upper() == "P":
        return "ensembl_protein"
    if ensembl_match and ensembl_match.group(1).upper() == "T":
        return "ensembl_transcript"
    if _UNIPROT_ID_RE.match(gene):
        return "uniprot_like"
    if upper == gene and any(char.isalpha() for char in gene):
        return "symbol_like"
    return "unknown"


def mapping_family_for_detected_type(id_type: str) -> str:
    if id_type.startswith("ensembl"):
        return "ensembl"
    if id_type == "uniprot_like":
        return "uniprot"
    if id_type == "entrez":
        return "entrez"
    return "symbol"


def normalize_gene_lookup_key(value: str) -> str:
    key = str(value).strip()
    if _ENSEMBL_ID_RE.match(key):
        key = key.split(".", 1)[0]
    if _UNIPROT_ID_RE.match(key):
        key = key.split("-", 1)[0]
    return key.upper()


def parse_gene_lines(text: str) -> list[str]:
    return clean_gene_list(text.replace("\r", "\n").replace(",", "\n").split("\n"))


def parse_ranked_text(text: str) -> list[tuple[str, float]]:
    rows: list[tuple[str, float]] = []
    for line_no, line in enumerate(text.replace("\r", "\n").split("\n"), start=1):
        line = line.strip()
        if not line:
            continue
        parts = line.replace(",", "\t").split()
        if len(parts) < 2:
            raise ValueError(f"ranked list line {line_no} must contain a gene and score")
        try:
            score = float(parts[1])
        except ValueError as exc:
            raise ValueError(f"ranked list line {line_no} has a non-numeric score") from exc
        if not math.isfinite(score):
            raise ValueError(f"ranked list line {line_no} has a non-finite score")
        rows.append((parts[0], score))
    return sorted(rows, key=lambda row: row[1], reverse=True)


def load_gene_ids(path: Path) -> set[str]:
    with path.open(encoding="utf-8") as handle:
        return {line.strip() for line in handle if line.strip()}


def load_gene_ids_with_fingerprint(
    path: Path,
    *,
    attempts: int = 3,
) -> tuple[set[str], FileFingerprint]:
    resolved = path.expanduser().resolve()
    for _attempt in range(attempts):
        before = resolved.stat()
        data = resolved.read_bytes()
        after = resolved.stat()
        if before.st_mtime_ns == after.st_mtime_ns and before.st_size == after.st_size:
            fingerprint = FileFingerprint(
                path=str(resolved),
                mtime_ns=before.st_mtime_ns,
                size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
            )
            genes = {line.strip() for line in data.decode("utf-8").splitlines() if line.strip()}
            return genes, fingerprint
    raise OSError(f"gene list changed while reading stable fingerprint: {resolved}")


class FileCacheKey(NamedTuple):
    path: str
    mtime_ns: int
    size: int


class FileFingerprint(NamedTuple):
    path: str
    mtime_ns: int
    size: int
    sha256: str


class FileSnapshot(NamedTuple):
    fingerprint: FileFingerprint
    snapshot_path: Path


def _file_cache_key(path: Path) -> FileCacheKey:
    resolved = path.expanduser().resolve()
    stat = resolved.stat()
    return FileCacheKey(path=str(resolved), mtime_ns=stat.st_mtime_ns, size=stat.st_size)


@lru_cache(maxsize=2)
def _load_embedding_cached(
    embedding_key: FileCacheKey,
    gene_list_key: FileCacheKey,
    normalize_rows: Callable[[np.ndarray], np.ndarray],
) -> tuple[np.ndarray, tuple[str, ...]]:
    raw = np.loadtxt(embedding_key.path, delimiter=",", dtype=np.float32)
    if raw.ndim == 1:
        raw = raw.reshape(1, -1)
    with Path(gene_list_key.path).open(encoding="utf-8") as handle:
        genes = tuple(line.strip() for line in handle if line.strip())
    if len(genes) != raw.shape[0]:
        raise ValueError(
            f"embedding row count ({raw.shape[0]}) does not match gene list ({len(genes)})"
        )
    matrix = np.ascontiguousarray(normalize_rows(raw), dtype=np.float32)
    matrix.setflags(write=False)
    return matrix, genes


def load_alias_map(path: Path, known_genes: set[str]) -> dict[str, str]:
    alias_path = path.expanduser().resolve()
    if not alias_path.exists():
        raise FileNotFoundError(f"alias path does not exist: {alias_path}")
    alias_to_gene: dict[str, str] = {}
    with alias_path.open(encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            delimiter = "\t" if "\t" in line else ","
            fields = [field.strip() for field in line.split(delimiter) if field.strip()]
            if len(fields) < 2:
                continue
            lowered = {field.lower() for field in fields}
            if {"alias", "gene"} & lowered or {"alias", "canonical"} & lowered:
                continue
            canonical = next((field for field in fields if field in known_genes), None)
            if canonical is None:
                continue
            for alias in fields:
                if alias != canonical:
                    alias_to_gene[alias] = canonical
                    alias_to_gene[alias.upper()] = canonical
    return alias_to_gene


def ensure_gene_mapping_sqlite(
    *,
    mapping_path: Path,
    sqlite_path: Path,
    gene_list_path: Path,
    known_genes: set[str] | None = None,
    gene_list_fingerprint: FileFingerprint | None = None,
) -> Path:
    mapping_fingerprint = _stable_file_fingerprint(mapping_path)
    if gene_list_fingerprint is None:
        known_genes, gene_list_fingerprint = load_gene_ids_with_fingerprint(gene_list_path)
    elif known_genes is None:
        known_genes = load_gene_ids(gene_list_path)
    assert known_genes is not None
    resolved_sqlite_path = sqlite_path.expanduser().resolve()
    metadata = _gene_mapping_sqlite_metadata(mapping_fingerprint, gene_list_fingerprint)
    if _gene_mapping_sqlite_current(resolved_sqlite_path, metadata):
        return resolved_sqlite_path

    with _gene_mapping_sqlite_lock(resolved_sqlite_path):
        resolved_sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = resolved_sqlite_path.with_name(
            f"{resolved_sqlite_path.name}.{os.getpid()}.{id(metadata)}.tmp"
        )
        snapshot_path = resolved_sqlite_path.with_name(
            f"{resolved_sqlite_path.name}.{os.getpid()}.{id(metadata)}.mapping-snapshot"
        )
        temp_path.unlink(missing_ok=True)
        snapshot_path.unlink(missing_ok=True)

        try:
            mapping_snapshot = _copy_file_snapshot(mapping_path, snapshot_path)
            metadata = _gene_mapping_sqlite_metadata(
                mapping_snapshot.fingerprint,
                gene_list_fingerprint,
            )
            if _gene_mapping_sqlite_current(resolved_sqlite_path, metadata):
                return resolved_sqlite_path

            conn = sqlite3.connect(temp_path)
            try:
                _initialize_gene_mapping_schema(conn)
                build = _load_gene_mapping_rows(conn, mapping_snapshot.snapshot_path, known_genes)
                for key, value in metadata.items():
                    conn.execute(
                        "INSERT INTO metadata(key, value) VALUES (?, ?)",
                        (key, value),
                    )
                conn.execute(
                    "INSERT INTO metadata(key, value) VALUES (?, ?)",
                    ("alias_rows", str(build.inserted_rows)),
                )
                conn.execute(
                    "INSERT INTO metadata(key, value) VALUES (?, ?)",
                    ("selected_source_columns", json.dumps(build.selected_source_columns)),
                )
                conn.execute(
                    "INSERT INTO metadata(key, value) VALUES (?, ?)",
                    ("ignored_source_columns", json.dumps(build.ignored_source_columns)),
                )
                conn.commit()
            finally:
                conn.close()
            temp_path.replace(resolved_sqlite_path)
        finally:
            temp_path.unlink(missing_ok=True)
            snapshot_path.unlink(missing_ok=True)
    return resolved_sqlite_path


def gene_mapping_provenance(
    *,
    sqlite_path: Path,
    species: str,
    canonical_id_namespace: str,
    alias_path: Path | None = None,
) -> dict[str, object]:
    resolved_sqlite_path = sqlite_path.expanduser().resolve()
    conn = sqlite3.connect(f"file:{resolved_sqlite_path}?mode=ro", uri=True)
    try:
        metadata = {
            str(key): str(value)
            for key, value in conn.execute("SELECT key, value FROM metadata").fetchall()
        }
    finally:
        conn.close()

    mapping_path = metadata.get("mapping_path")
    gene_list_path = metadata.get("gene_list_path")
    return {
        "species": species,
        "canonical_id_namespace": canonical_id_namespace,
        "schema_version": metadata.get("schema_version"),
        "normalizer_version": metadata.get("normalizer_version"),
        "mapping_file": Path(mapping_path).name if mapping_path else None,
        "mapping_mtime_ns": _metadata_int(metadata.get("mapping_mtime_ns")),
        "mapping_size": _metadata_int(metadata.get("mapping_size")),
        "mapping_sha256": metadata.get("mapping_sha256"),
        "gene_list_file": Path(gene_list_path).name if gene_list_path else None,
        "gene_list_mtime_ns": _metadata_int(metadata.get("gene_list_mtime_ns")),
        "gene_list_size": _metadata_int(metadata.get("gene_list_size")),
        "gene_list_sha256": metadata.get("gene_list_sha256"),
        "sqlite_file": resolved_sqlite_path.name,
        "alias_rows": _metadata_int(metadata.get("alias_rows")),
        "selected_source_columns": _metadata_json(metadata.get("selected_source_columns"), []),
        "ignored_source_columns": _metadata_json(metadata.get("ignored_source_columns"), []),
        "alias_file": file_provenance(alias_path) if alias_path is not None else None,
    }


def _metadata_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _metadata_json(value: str | None, fallback: object) -> object:
    if value is None:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _gene_mapping_sqlite_metadata(
    mapping_fingerprint: FileFingerprint,
    gene_list_fingerprint: FileFingerprint,
) -> dict[str, str]:
    return {
        "schema_version": _GENE_MAPPING_SQLITE_SCHEMA_VERSION,
        "normalizer_version": _GENE_MAPPING_NORMALIZER_VERSION,
        "mapping_path": mapping_fingerprint.path,
        "mapping_mtime_ns": str(mapping_fingerprint.mtime_ns),
        "mapping_size": str(mapping_fingerprint.size),
        "mapping_sha256": mapping_fingerprint.sha256,
        "gene_list_path": gene_list_fingerprint.path,
        "gene_list_mtime_ns": str(gene_list_fingerprint.mtime_ns),
        "gene_list_size": str(gene_list_fingerprint.size),
        "gene_list_sha256": gene_list_fingerprint.sha256,
    }


def _stable_file_fingerprint(path: Path, *, attempts: int = 3) -> FileFingerprint:
    resolved = path.expanduser().resolve()
    for _attempt in range(attempts):
        before = resolved.stat()
        digest = hashlib.sha256()
        size = 0
        with resolved.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                size += len(chunk)
                digest.update(chunk)
        after = resolved.stat()
        if (
            before.st_mtime_ns == after.st_mtime_ns
            and before.st_size == after.st_size
            and before.st_size == size
        ):
            return FileFingerprint(
                path=str(resolved),
                mtime_ns=before.st_mtime_ns,
                size=size,
                sha256=digest.hexdigest(),
            )
    raise OSError(f"file changed while reading stable fingerprint: {resolved}")


def _copy_file_snapshot(
    source_path: Path,
    snapshot_path: Path,
    *,
    attempts: int = 3,
) -> FileSnapshot:
    resolved_source_path = source_path.expanduser().resolve()
    for _attempt in range(attempts):
        before = resolved_source_path.stat()
        digest = hashlib.sha256()
        size = 0
        snapshot_path.unlink(missing_ok=True)
        with resolved_source_path.open("rb") as source, snapshot_path.open("wb") as snapshot:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                size += len(chunk)
                digest.update(chunk)
                snapshot.write(chunk)
        after = resolved_source_path.stat()
        if (
            before.st_mtime_ns == after.st_mtime_ns
            and before.st_size == after.st_size
            and before.st_size == size
        ):
            return FileSnapshot(
                fingerprint=FileFingerprint(
                    path=str(resolved_source_path),
                    mtime_ns=before.st_mtime_ns,
                    size=size,
                    sha256=digest.hexdigest(),
                ),
                snapshot_path=snapshot_path,
            )
    snapshot_path.unlink(missing_ok=True)
    raise OSError(f"file changed while copying stable snapshot: {resolved_source_path}")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_provenance(path: Path | None) -> dict[str, object] | None:
    if path is None:
        return None
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        return {"path": str(resolved), "file": resolved.name, "exists": False}
    fingerprint = _stable_file_fingerprint(resolved)
    return {
        "path": str(resolved),
        "file": resolved.name,
        "exists": True,
        "mtime_ns": fingerprint.mtime_ns,
        "size": fingerprint.size,
        "sha256": fingerprint.sha256,
    }


def validate_gene_mapping_file(
    *,
    mapping_path: Path,
    sqlite_path: Path,
    gene_list_path: Path,
    species: str,
    canonical_id_namespace: str = "entrez",
) -> dict[str, object]:
    known_genes, gene_list_fingerprint = load_gene_ids_with_fingerprint(gene_list_path)
    resolved_sqlite_path = ensure_gene_mapping_sqlite(
        mapping_path=mapping_path,
        sqlite_path=sqlite_path,
        gene_list_path=gene_list_path,
        known_genes=known_genes,
        gene_list_fingerprint=gene_list_fingerprint,
    )
    return gene_mapping_sqlite_summary(
        sqlite_path=resolved_sqlite_path,
        known_genes=known_genes,
        species=species,
        canonical_id_namespace=canonical_id_namespace,
    )


def gene_mapping_sqlite_summary(
    *,
    sqlite_path: Path,
    known_genes: set[str],
    species: str,
    canonical_id_namespace: str = "entrez",
) -> dict[str, object]:
    resolved_sqlite_path = sqlite_path.expanduser().resolve()
    conn = sqlite3.connect(f"file:{resolved_sqlite_path}?mode=ro", uri=True)
    try:
        metadata = {
            str(key): str(value)
            for key, value in conn.execute("SELECT key, value FROM metadata").fetchall()
        }
        alias_rows, mapped_entrez_count, alias_count = conn.execute(
            """
            SELECT
                count(*),
                count(DISTINCT entrez),
                count(DISTINCT normalized_alias)
            FROM aliases
            """
        ).fetchone()
        rows_by_type = {
            str(id_type): int(count)
            for id_type, count in conn.execute(
                "SELECT id_type, count(*) FROM aliases GROUP BY id_type ORDER BY id_type"
            ).fetchall()
        }
        mapped_entrez = {
            str(row[0]) for row in conn.execute("SELECT DISTINCT entrez FROM aliases").fetchall()
        }
        ambiguous_rows = conn.execute(
            """
            WITH ambiguous AS (
                SELECT
                    normalized_alias,
                    count(DISTINCT entrez) AS candidate_count,
                    group_concat(DISTINCT entrez) AS candidates,
                    group_concat(DISTINCT id_type) AS id_types
                FROM aliases
                GROUP BY normalized_alias
                HAVING candidate_count > 1
            )
            SELECT count(*) FROM ambiguous
            """
        ).fetchone()
        ambiguous_alias_count = int(ambiguous_rows[0] or 0)
        ambiguous_examples = [
            {
                "alias": str(alias),
                "candidate_count": int(candidate_count),
                "candidates": sorted(str(candidates).split(","), key=_gene_sort_key),
                "id_types": sorted(str(id_types).split(",")),
            }
            for alias, candidate_count, candidates, id_types in conn.execute(
                """
                SELECT
                    normalized_alias,
                    count(DISTINCT entrez) AS candidate_count,
                    group_concat(DISTINCT entrez) AS candidates,
                    group_concat(DISTINCT id_type) AS id_types
                FROM aliases
                GROUP BY normalized_alias
                HAVING candidate_count > 1
                ORDER BY candidate_count DESC, normalized_alias
                LIMIT 20
                """
            ).fetchall()
        ]
    finally:
        conn.close()

    missing_entrez_ids = sorted(known_genes - mapped_entrez, key=_gene_sort_key)
    embedding_gene_count = len(known_genes)
    return {
        "species": species,
        "canonical_id_namespace": canonical_id_namespace,
        "sqlite_path": str(resolved_sqlite_path),
        "mapping_path": metadata.get("mapping_path"),
        "gene_list_path": metadata.get("gene_list_path"),
        "metadata": metadata,
        "selected_source_columns": _metadata_json(metadata.get("selected_source_columns"), []),
        "ignored_source_columns": _metadata_json(metadata.get("ignored_source_columns"), []),
        "embedding_gene_count": embedding_gene_count,
        "mapped_entrez_count": int(mapped_entrez_count or 0),
        "missing_entrez_count": len(missing_entrez_ids),
        "missing_entrez_examples": missing_entrez_ids[:25],
        "coverage": (
            (int(mapped_entrez_count or 0) / embedding_gene_count) if embedding_gene_count else 0.0
        ),
        "alias_rows": int(alias_rows or 0),
        "alias_count": int(alias_count or 0),
        "alias_rows_by_type": rows_by_type,
        "ambiguous_alias_count": ambiguous_alias_count,
        "ambiguous_alias_examples": ambiguous_examples,
    }


def _gene_sort_key(value: str) -> tuple[int, str]:
    return (0, f"{int(value):020d}") if value.isdigit() else (1, value)


def _gene_mapping_sqlite_current(sqlite_path: Path, metadata: dict[str, str]) -> bool:
    if not sqlite_path.exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
        try:
            rows = conn.execute("SELECT key, value FROM metadata").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return False
    existing = {str(key): str(value) for key, value in rows}
    return all(existing.get(key) == value for key, value in metadata.items())


def _initialize_gene_mapping_schema(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute(
        """
        CREATE TABLE aliases(
            normalized_alias TEXT NOT NULL,
            alias TEXT NOT NULL,
            entrez TEXT NOT NULL,
            id_type TEXT NOT NULL,
            source_column TEXT NOT NULL,
            PRIMARY KEY(normalized_alias, entrez, id_type, source_column)
        )
        """
    )
    conn.execute("CREATE INDEX aliases_normalized_alias_idx ON aliases(normalized_alias)")


class GeneMappingBuild(NamedTuple):
    inserted_rows: int
    selected_source_columns: list[dict[str, str]]
    ignored_source_columns: list[str]


def _load_gene_mapping_rows(
    conn: sqlite3.Connection,
    mapping_path: Path,
    known_genes: set[str],
) -> GeneMappingBuild:
    with mapping_path.open(encoding="utf-8", newline="") as handle:
        first_line = handle.readline()
        delimiter = "\t" if "\t" in first_line else ","
        handle.seek(0)
        reader = csv.DictReader(handle, delimiter=delimiter)
        if not reader.fieldnames:
            raise ValueError(f"gene mapping file has no header: {mapping_path}")
        fieldnames = [field.strip() for field in reader.fieldnames]
        reader.fieldnames = fieldnames
        entrez_field = _gene_mapping_entrez_field(fieldnames)
        selected_columns = [
            {"column": field, "id_type": id_type}
            for field in fieldnames
            if (id_type := gene_mapping_column_id_type(field)) is not None
        ]
        ignored_columns = [
            field for field in fieldnames if gene_mapping_column_id_type(field) is None
        ]
        inserted = 0
        batch: list[tuple[str, str, str, str, str]] = []
        for row in reader:
            entrez = str(row.get(entrez_field, "")).strip()
            if not entrez or entrez not in known_genes:
                continue
            for source_column in fieldnames:
                id_type = gene_mapping_column_id_type(source_column)
                if id_type is None:
                    continue
                raw_value = str(row.get(source_column, "")).strip()
                for alias in split_gene_mapping_values(raw_value):
                    normalized = normalize_gene_lookup_key(alias)
                    if not normalized:
                        continue
                    batch.append((normalized, alias, entrez, id_type, source_column))
            if len(batch) >= 5000:
                inserted += _insert_gene_mapping_batch(conn, batch)
                batch.clear()
        if batch:
            inserted += _insert_gene_mapping_batch(conn, batch)
        return GeneMappingBuild(
            inserted_rows=inserted,
            selected_source_columns=selected_columns,
            ignored_source_columns=ignored_columns,
        )


def _validate_gene_mapping_overlap(
    *,
    sqlite_path: Path,
    known_genes: set[str],
    min_overlap: float,
) -> None:
    if min_overlap <= 0 or not known_genes:
        return
    resolved_sqlite_path = sqlite_path.expanduser().resolve()
    conn = sqlite3.connect(f"file:{resolved_sqlite_path}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT count(DISTINCT entrez) FROM aliases").fetchone()
    finally:
        conn.close()
    mapped_count = int(row[0] or 0)
    overlap = mapped_count / len(known_genes)
    if overlap < min_overlap:
        raise ValueError(
            "gene mapping canonical IDs overlap only "
            f"{overlap:.3%} of the embedding gene list; expected at least {min_overlap:.3%}"
        )


def _insert_gene_mapping_batch(
    conn: sqlite3.Connection,
    batch: list[tuple[str, str, str, str, str]],
) -> int:
    before = conn.total_changes
    conn.executemany(
        """
        INSERT OR IGNORE INTO aliases(
            normalized_alias,
            alias,
            entrez,
            id_type,
            source_column
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        batch,
    )
    return conn.total_changes - before


def _gene_mapping_entrez_field(fieldnames: list[str]) -> str:
    for field in fieldnames:
        normalized = field.strip().lower()
        if normalized in _GENE_MAPPING_ENTREZ_COLUMNS:
            return field
    raise ValueError("gene mapping file must contain an entrez column")


def gene_mapping_column_id_type(column: str) -> str | None:
    normalized = column.strip().lower()
    return _GENE_MAPPING_COLUMN_TYPES.get(normalized)


def split_gene_mapping_values(value: str) -> list[str]:
    stripped = value.strip()
    if not stripped or stripped in {"-", "NA", "N/A", "None", "none"}:
        return []
    pieces = [stripped]
    for separator in ("|", ";"):
        next_pieces: list[str] = []
        for piece in pieces:
            next_pieces.extend(piece.split(separator))
        pieces = next_pieces
    return [piece.strip() for piece in pieces if piece.strip()]


def validate_gene_ids(genes: Iterable[str], known_genes: set[str]) -> tuple[list[str], list[str]]:
    cleaned = clean_gene_list(genes)
    valid = [gene for gene in cleaned if gene in known_genes]
    invalid = [gene for gene in cleaned if gene not in known_genes]
    return valid, invalid


def normalize_gmt_text(text: str, mapper: GeneIdMapper) -> tuple[str, GeneIdMapping]:
    lines: list[str] = []
    all_records: list[GeneIdMapRecord] = []
    mapped_all: list[str] = []
    unmapped_all: list[str] = []
    for line_no, raw_line in enumerate(text.replace("\r", "\n").split("\n"), start=1):
        line = raw_line.strip()
        if not line:
            continue
        fields = [field.strip() for field in line.split("\t")]
        if len(fields) < 3:
            raise ValueError(
                f"GMT line {line_no} must contain term, description, and at least one gene"
            )
        mapping = mapper.map_many(fields[2:])
        all_records.extend(mapping.records)
        mapped_all.extend(mapping.mapped)
        unmapped_all.extend(mapping.unmapped)
        lines.append("\t".join([fields[0], fields[1], *mapping.mapped]))
    return (
        "\n".join(lines) + ("\n" if lines else ""),
        GeneIdMapping(
            mapped=mapped_all,
            unmapped=unmapped_all,
            records=all_records,
            provenance=mapper.mapping_provenance,
        ),
    )


def validate_gmt_text(
    text: str,
    *,
    known_genes: set[str],
    min_gene_set_size: int,
    max_gene_set_size: int,
    max_terms: int = 20000,
) -> GmtValidation:
    seen_terms: set[str] = set()
    all_genes: set[str] = set()
    matched_genes: set[str] = set()
    usable_terms = 0
    term_count = 0

    for line_no, raw_line in enumerate(text.replace("\r", "\n").split("\n"), start=1):
        line = raw_line.strip()
        if not line:
            continue
        fields = [field.strip() for field in line.split("\t")]
        if len(fields) < 3:
            raise ValueError(
                f"GMT line {line_no} must contain term, description, and at least one gene"
            )
        term = fields[0]
        if not term:
            raise ValueError(f"GMT line {line_no} has an empty term identifier")
        if term in seen_terms:
            raise ValueError(f"GMT term {term!r} is duplicated")
        seen_terms.add(term)
        genes = [gene for gene in fields[2:] if gene]
        if not genes:
            raise ValueError(f"GMT line {line_no} has no genes")

        unique_genes = set(genes)
        all_genes.update(unique_genes)
        matched = unique_genes & known_genes
        matched_genes.update(matched)
        if min_gene_set_size <= len(matched) <= max_gene_set_size:
            usable_terms += 1
        term_count += 1
        if term_count > max_terms:
            raise ValueError(f"GMT file has more than {max_terms} terms")

    if term_count == 0:
        raise ValueError("GMT file is empty")
    if not matched_genes:
        raise ValueError("GMT file has no genes present in the embedding gene list")
    if usable_terms == 0:
        raise ValueError("GMT file has no terms that survive the current size filters")

    return GmtValidation(
        term_count=term_count,
        usable_term_count=usable_terms,
        gene_count=len(all_genes),
        matched_gene_count=len(matched_genes),
    )


def parse_obo_text(text: str) -> tuple[dict[str, GoTerm], dict[str, str]]:
    terms: dict[str, GoTerm] = {}
    alt_ids: dict[str, str] = {}
    current_id: str | None = None
    current_name = ""
    current_namespace = ""
    current_parents: list[str] = []
    current_alt_ids: list[str] = []
    current_obsolete = False

    def flush() -> None:
        nonlocal current_id
        if current_id is None or current_obsolete:
            return
        go_id = current_id.strip()
        if not go_id:
            return
        terms[go_id] = GoTerm(
            go_id=go_id,
            name=current_name.strip() or go_id,
            namespace=current_namespace.strip(),
            parents=tuple(current_parents),
        )
        for alt_id in current_alt_ids:
            alt_ids[alt_id] = go_id

    for raw_line in text.replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line or line.startswith("!"):
            continue
        if line == "[Term]":
            flush()
            current_id = ""
            current_name = ""
            current_namespace = ""
            current_parents = []
            current_alt_ids = []
            current_obsolete = False
            continue
        if line.startswith("["):
            flush()
            current_id = None
            continue
        if current_id is None or ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if key == "id":
            current_id = value
        elif key == "name":
            current_name = value
        elif key == "namespace":
            current_namespace = value
        elif key == "alt_id":
            current_alt_ids.append(value)
        elif key == "is_obsolete" and value == "true":
            current_obsolete = True
        elif key == "is_a":
            current_parents.append(value.split()[0])
        elif key == "relationship":
            pieces = value.split()
            if len(pieces) >= 2 and pieces[0] == "part_of":
                current_parents.append(pieces[1])
    flush()

    if not terms:
        raise ValueError("OBO file contains no active [Term] entries")
    return terms, alt_ids


def _annotation_rows(text: str) -> Iterable[tuple[list[str], str]]:
    for line_no, raw_line in enumerate(text.replace("\r", "\n").split("\n"), start=1):
        line = raw_line.strip()
        if not line or line.startswith(("!", "#")):
            continue
        fields = [field.strip() for field in line.split("\t")]
        if len(fields) >= 5 and fields[4].startswith("GO:"):
            qualifiers = set(fields[3].split("|")) if fields[3] else set()
            if "NOT" in qualifiers:
                continue
            gene_candidates = [fields[1]]
            if len(fields) > 2:
                gene_candidates.append(fields[2])
            yield (gene_candidates, fields[4])
            continue

        fields = [field.strip() for field in line.replace(",", "\t").split("\t")]
        fields = [field for field in fields if field]
        if len(fields) < 2:
            raise ValueError(f"annotation line {line_no} must contain a gene and GO term")
        if fields[0].startswith("GO:"):
            yield ([fields[1]], fields[0])
        elif fields[1].startswith("GO:"):
            yield ([fields[0]], fields[1])
        else:
            raise ValueError(f"annotation line {line_no} must contain a GO term")


def go_obo_annotations_to_gmt_text(
    *,
    obo_text: str,
    annotation_text: str,
    known_genes: set[str],
    mapper: GeneIdMapper | None = None,
    namespace: str = "biological_process",
    propagate: bool = True,
) -> tuple[str, GeneIdMapping]:
    terms, alt_ids = parse_obo_text(obo_text)
    mapper = mapper or GeneIdMapper(known_genes)
    namespace = namespace.strip()
    selected_namespace = "" if namespace.lower() in {"", "all", "any"} else namespace
    term_to_genes: dict[str, set[str]] = {}
    ancestor_cache: dict[str, set[str]] = {}
    records: list[GeneIdMapRecord] = []
    mapped_all: list[str] = []
    unmapped_all: list[str] = []

    def normalize(go_id: str) -> str:
        return alt_ids.get(go_id, go_id)

    def ancestors(go_id: str) -> set[str]:
        go_id = normalize(go_id)
        if go_id in ancestor_cache:
            return ancestor_cache[go_id]
        parents: set[str] = set()
        for parent in terms.get(go_id, GoTerm(go_id, go_id, "", ())).parents:
            normalized_parent = normalize(parent)
            if normalized_parent in terms:
                parents.add(normalized_parent)
                parents.update(ancestors(normalized_parent))
        ancestor_cache[go_id] = parents
        return parents

    def include_term(go_id: str) -> bool:
        term = terms.get(go_id)
        if term is None:
            return False
        return not selected_namespace or term.namespace == selected_namespace

    matched_annotations = 0
    for gene_candidates, raw_go_id in _annotation_rows(annotation_text):
        candidate_mapping = mapper.map_records(gene_candidates)
        records.extend(candidate_mapping)
        mapped_gene = next((record.mapped for record in candidate_mapping if record.mapped), None)
        if mapped_gene is None:
            unmapped_all.extend(record.submitted for record in candidate_mapping)
            continue
        mapped_all.append(mapped_gene)
        go_id = normalize(raw_go_id)
        if go_id not in terms:
            continue
        matched_annotations += 1
        targets = {go_id}
        if propagate:
            targets.update(ancestors(go_id))
        for target in targets:
            if include_term(target):
                term_to_genes.setdefault(target, set()).add(mapped_gene)

    if matched_annotations == 0:
        raise ValueError("GO annotations have no genes present in the embedding gene list")
    if not term_to_genes:
        raise ValueError("GO annotations have no terms in the selected namespace")

    lines: list[str] = []
    for go_id in sorted(term_to_genes):
        genes = sorted(term_to_genes[go_id])
        if genes:
            lines.append("\t".join([go_id, terms[go_id].name, *genes]))
    if not lines:
        raise ValueError("GO annotations produced no non-empty gene sets")
    return (
        "\n".join(lines) + "\n",
        GeneIdMapping(
            mapped=mapped_all,
            unmapped=unmapped_all,
            records=records,
            provenance=mapper.mapping_provenance,
        ),
    )


def load_embedding(
    embedding_path: Path, gene_list_path: Path, func_optimized
) -> tuple[np.ndarray, list[str]]:
    matrix, genes = _load_embedding_cached(
        _file_cache_key(embedding_path),
        _file_cache_key(gene_list_path),
        func_optimized.l2_normalize_rows,
    )
    return matrix, list(genes)
