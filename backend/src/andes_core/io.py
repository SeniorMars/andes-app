from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

import numpy as np


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


@dataclass(frozen=True)
class GeneIdMapping:
    mapped: list[str]
    unmapped: list[str]
    records: list[GeneIdMapRecord]

    @property
    def id_type_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in self.records:
            counts[record.id_type] = counts.get(record.id_type, 0) + 1
        return counts


class GeneIdMapper:
    def __init__(self, known_genes: set[str], alias_to_gene: dict[str, str] | None = None):
        self.known_genes = known_genes
        self.alias_to_gene = alias_to_gene or {}

    @classmethod
    def from_paths(cls, gene_list_path: Path, alias_path: Path | None = None) -> GeneIdMapper:
        known_genes = load_gene_ids(gene_list_path)
        alias_to_gene = load_alias_map(alias_path, known_genes) if alias_path else {}
        return cls(known_genes, alias_to_gene)

    def map_one(self, gene: str) -> GeneIdMapRecord:
        value = str(gene).strip()
        id_type = detect_gene_id_type(value)
        if value in self.known_genes:
            return GeneIdMapRecord(value, value, id_type, "embedding")
        mapped = self.alias_to_gene.get(value)
        if mapped is None:
            mapped = self.alias_to_gene.get(value.upper())
        if mapped is not None:
            return GeneIdMapRecord(value, mapped, id_type, "alias")
        return GeneIdMapRecord(value, None, id_type, "unmapped")

    def map_many(self, genes: Iterable[str]) -> GeneIdMapping:
        seen: set[str] = set()
        mapped: list[str] = []
        unmapped: list[str] = []
        records: list[GeneIdMapRecord] = []
        for gene in clean_gene_list(genes):
            record = self.map_one(gene)
            records.append(record)
            if record.mapped is None:
                unmapped.append(record.submitted)
                continue
            if record.mapped not in seen:
                seen.add(record.mapped)
                mapped.append(record.mapped)
        return GeneIdMapping(mapped=mapped, unmapped=unmapped, records=records)


class GoTerm(NamedTuple):
    go_id: str
    name: str
    namespace: str
    parents: tuple[str, ...]


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
    if upper.startswith("ENSG"):
        return "ensembl_gene"
    if upper.startswith("ENSP"):
        return "ensembl_protein"
    if upper.startswith("ENST"):
        return "ensembl_transcript"
    if len(upper) in {6, 10} and upper[0].isalpha() and any(char.isdigit() for char in upper):
        return "uniprot_like"
    if upper == gene and any(char.isalpha() for char in gene):
        return "symbol_like"
    return "unknown"


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
            rows.append((parts[0], float(parts[1])))
        except ValueError as exc:
            raise ValueError(f"ranked list line {line_no} has a non-numeric score") from exc
    return sorted(rows, key=lambda row: row[1], reverse=True)


def load_gene_ids(path: Path) -> set[str]:
    with path.open(encoding="utf-8") as handle:
        return {line.strip() for line in handle if line.strip()}


class FileCacheKey(NamedTuple):
    path: str
    mtime_ns: int
    size: int


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
        GeneIdMapping(mapped=mapped_all, unmapped=unmapped_all, records=all_records),
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
        candidate_mapping = [mapper.map_one(gene) for gene in gene_candidates]
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
        GeneIdMapping(mapped=mapped_all, unmapped=unmapped_all, records=records),
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
