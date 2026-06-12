from __future__ import annotations

import contextlib
import csv
import hashlib
import math
import time
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np
from scipy.stats import norm
from statsmodels.stats.multitest import multipletests

from .config import AndesSettings, get_settings
from .io import clean_gene_list, load_embedding
from .legacy import load_legacy_modules
from .schemas import (
    AnalysisKind,
    AnalysisResult,
    GseaRequest,
    ResultTerm,
    SetSimilarityRequest,
)

fcntl_module: Any | None
try:
    import fcntl as fcntl_module
except ImportError:  # pragma: no cover - non-Unix fallback
    fcntl_module = None


class GeneCounts(NamedTuple):
    input_count: int
    matched_count: int
    unmapped: list[str]
    id_type_counts: dict[str, int]


class CacheBuild(NamedTuple):
    cache: object
    profile: dict[str, object]


class SeedResolution(NamedTuple):
    seed: int
    strategy: str


@contextlib.contextmanager
def cache_file_lock(cache_path: Path):
    lock_path = cache_path.with_name(f"{cache_path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        if fcntl_module is not None:
            fcntl_module.flock(handle.fileno(), fcntl_module.LOCK_EX)
        try:
            yield
        finally:
            if fcntl_module is not None:
                fcntl_module.flock(handle.fileno(), fcntl_module.LOCK_UN)


class AndesEngine:
    def __init__(self, settings: AndesSettings | None = None):
        self.settings = settings or get_settings()
        self.legacy = load_legacy_modules(self.settings.original_src)

    def run_set_similarity(
        self, request: SetSimilarityRequest, artifact_dir: Path | None = None
    ) -> AnalysisResult:
        total_start = time.perf_counter()
        paths = self._resolve_paths(request)
        func = self.legacy.func_optimized
        ld = self.legacy.load_data
        workers = request.workers or self.settings.workers
        seed = request.seed if request.seed is not None else self.settings.seed
        ite = request.null_iterations or self.settings.null_iterations

        e_unit, node_list = load_embedding(paths["embedding"], paths["gene_list"], func)
        node_set = set(node_list)
        node2index = {gene: i for i, gene in enumerate(node_list)}

        if request.query_gene_set_path is not None:
            return self._run_collection_similarity(
                request=request,
                paths=paths,
                func=func,
                ld=ld,
                e_unit=e_unit,
                node_set=node_set,
                node2index=node2index,
                workers=workers,
                seed=seed,
                ite=ite,
                artifact_dir=artifact_dir,
            )

        if request.genes is None:
            raise ValueError(
                "input genes are required when no query gene-set collection is provided"
            )
        input_genes = clean_gene_list(request.genes)
        valid_genes = [gene for gene in input_genes if gene in node2index]
        invalid_genes = [gene for gene in input_genes if gene not in node2index]
        gene_counts = self._gene_counts_from_mapping(
            request.id_mapping.get("genes"),
            input_count=len(input_genes),
            matched_count=len(valid_genes),
            unmapped=invalid_genes,
        )
        if not valid_genes:
            raise ValueError("none of the input genes are present in the embedding gene list")

        input_idx = np.asarray([node2index[gene] for gene in valid_genes], dtype=np.int32)

        gene_sets = ld.load_gmt(str(paths["gene_set"]))
        term_names = ld.term2name(str(paths["gene_set"]))
        target_indices = ld.term2indexes(
            gene_sets,
            node2index,
            upper=request.max_gene_set_size,
            lower=request.min_gene_set_size,
        )
        target_indices_np = func.preconvert_indices_to_arrays(target_indices)
        target_terms = sorted(target_indices_np.keys())
        if not target_terms:
            raise ValueError("no gene-set terms survived the size filters")
        self._enforce_pair_limit(len(target_terms))

        if request.background_genes:
            background = [
                gene for gene in clean_gene_list(request.background_genes) if gene in node2index
            ]
            if not background:
                raise ValueError("background gene list had no genes present in the embedding")
            pop = np.asarray(sorted(node2index[gene] for gene in background), dtype=np.int32)
        else:
            pop = np.asarray(
                func.get_background_indices(gene_sets, node_set, node2index), dtype=np.int32
            )

        input_indices_np = {"input": input_idx}
        input_terms = ["input"]
        size_pairs = {(len(input_idx), len(target_indices_np[term])) for term in target_terms}
        cache_build = self._load_or_build_bma_cache(
            func, e_unit, pop, pop, size_pairs, ite, seed, workers
        )
        cache = cache_build.cache

        scoring_start = time.perf_counter()
        input_blocks = func.precompute_term_embedding_blocks(e_unit, input_indices_np)
        target_blocks = func.precompute_term_embedding_blocks(e_unit, target_indices_np)
        zscores, _stats = func.score_bma_zscore_matrix_bestmatch(
            e_unit,
            input_terms,
            target_terms,
            input_indices_np,
            target_indices_np,
            cache,
            blocks1=input_blocks,
            blocks2=target_blocks,
            symmetric=False,
            max_workspace_mb=self.settings.query_memory_mb,
            show_progress=False,
        )
        scoring_seconds = time.perf_counter() - scoring_start
        total_seconds = time.perf_counter() - total_start

        z = np.asarray(zscores[0], dtype=np.float64)
        p_values = 1 - norm.cdf(z)
        p_corrected = multipletests(p_values, method="fdr_bh")[1]
        rows = self._result_rows(
            terms=target_terms,
            term_names=term_names,
            sizes=[len(target_indices_np[term]) for term in target_terms],
            z_scores=z,
            p_values=p_values,
            p_corrected=p_corrected,
            true_scores=None,
        )

        result = AnalysisResult(
            kind=AnalysisKind.SET_SIMILARITY,
            results=rows,
            input_gene_count=gene_counts.input_count,
            valid_gene_count=gene_counts.matched_count,
            invalid_genes=gene_counts.unmapped,
            warnings=self._warnings(gene_counts.unmapped),
            parameters={
                "min_gene_set_size": request.min_gene_set_size,
                "max_gene_set_size": request.max_gene_set_size,
                "null_iterations": ite,
                "workers": workers,
                "seed": cache_build.profile.get("seed"),
                "seed_strategy": cache_build.profile.get("seed_strategy"),
                "mode": "gene_list",
                "gene_set_path": str(paths["gene_set"]),
                "target_term_count": len(target_terms),
                "total_pairs": len(target_terms),
                "id_mapping": request.id_mapping,
                "cache": cache_build.profile,
                "timing_seconds": self._timing_profile(
                    cache_build.profile, scoring_seconds, total_seconds
                ),
            },
        )
        self._write_result_downloads(artifact_dir, result)
        return result

    def _run_collection_similarity(
        self,
        *,
        request: SetSimilarityRequest,
        paths: dict[str, Path],
        func,
        ld,
        e_unit: np.ndarray,
        node_set: set[str],
        node2index: dict[str, int],
        workers: int,
        seed: int | None,
        ite: int,
        artifact_dir: Path | None,
    ) -> AnalysisResult:
        query_sets = ld.load_gmt(str(paths["query_gene_set"]))
        query_names = ld.term2name(str(paths["query_gene_set"]))
        query_indices = ld.term2indexes(
            query_sets,
            node2index,
            upper=request.max_gene_set_size,
            lower=request.min_gene_set_size,
        )
        query_indices_np = func.preconvert_indices_to_arrays(query_indices)
        query_terms = sorted(query_indices_np.keys())
        if not query_terms:
            raise ValueError("no query gene-set terms survived the size filters")

        target_sets = ld.load_gmt(str(paths["gene_set"]))
        target_names = ld.term2name(str(paths["gene_set"]))
        target_indices = ld.term2indexes(
            target_sets,
            node2index,
            upper=request.max_gene_set_size,
            lower=request.min_gene_set_size,
        )
        target_indices_np = func.preconvert_indices_to_arrays(target_indices)
        target_terms = sorted(target_indices_np.keys())
        if not target_terms:
            raise ValueError("no target gene-set terms survived the size filters")
        pair_count = len(query_terms) * len(target_terms)
        self._enforce_pair_limit(pair_count)

        bg1 = np.asarray(
            sorted(func.get_background_indices(query_sets, node_set, node2index)),
            dtype=np.int32,
        )
        bg2 = np.asarray(
            sorted(func.get_background_indices(target_sets, node_set, node2index)),
            dtype=np.int32,
        )
        sizes1 = {len(query_indices_np[term]) for term in query_terms}
        sizes2 = {len(target_indices_np[term]) for term in target_terms}
        size_pairs = {(query_size, target_size) for query_size in sizes1 for target_size in sizes2}
        total_start = time.perf_counter()
        cache_build = self._load_or_build_bma_cache(
            func, e_unit, bg1, bg2, size_pairs, ite, seed, workers
        )
        cache = cache_build.cache

        scoring_start = time.perf_counter()
        query_blocks = func.precompute_term_embedding_blocks(e_unit, query_indices_np)
        target_blocks = func.precompute_term_embedding_blocks(e_unit, target_indices_np)
        symmetric = np.array_equal(bg1, bg2) and func.same_index_arrays_by_term(
            query_terms,
            query_indices_np,
            target_terms,
            target_indices_np,
        )
        zscores, _stats = func.score_bma_zscore_matrix_bestmatch(
            e_unit,
            query_terms,
            target_terms,
            query_indices_np,
            target_indices_np,
            cache,
            blocks1=query_blocks,
            blocks2=target_blocks,
            symmetric=symmetric,
            max_workspace_mb=self.settings.query_memory_mb,
            show_progress=False,
        )
        scoring_seconds = time.perf_counter() - scoring_start
        total_seconds = time.perf_counter() - total_start

        flat_z = np.asarray(zscores, dtype=np.float64).reshape(-1)
        p_values = 1 - norm.cdf(flat_z)
        p_corrected = multipletests(p_values, method="fdr_bh")[1]
        max_rows = max(1, self.settings.max_result_rows)
        top_indices = np.argsort(flat_z)[::-1][:max_rows]
        rows: list[ResultTerm] = []
        target_count = len(target_terms)
        for flat_idx in top_indices:
            query_idx = int(flat_idx // target_count)
            target_idx = int(flat_idx % target_count)
            query_term = query_terms[query_idx]
            target_term = target_terms[target_idx]
            corrected = float(p_corrected[flat_idx])
            log10_p = -math.log10(corrected) if corrected > 0 else -math.log10(np.finfo(float).tiny)
            rows.append(
                ResultTerm(
                    term=f"{query_term} vs {target_term}",
                    description=target_names.get(target_term),
                    size=len(target_indices_np[target_term]),
                    query_term=query_term,
                    query_description=query_names.get(query_term),
                    query_size=len(query_indices_np[query_term]),
                    target_term=target_term,
                    target_description=target_names.get(target_term),
                    target_size=len(target_indices_np[target_term]),
                    z_score=float(flat_z[flat_idx]),
                    p_value=float(p_values[flat_idx]),
                    p_value_corrected=corrected,
                    log10_p_value_corrected=float(log10_p),
                    significant=corrected < 0.05,
                )
            )

        result = AnalysisResult(
            kind=AnalysisKind.SET_SIMILARITY,
            results=rows,
            input_gene_count=len(query_terms),
            valid_gene_count=len(query_terms),
            invalid_genes=[],
            warnings=[],
            parameters={
                "min_gene_set_size": request.min_gene_set_size,
                "max_gene_set_size": request.max_gene_set_size,
                "null_iterations": ite,
                "workers": workers,
                "seed": cache_build.profile.get("seed"),
                "seed_strategy": cache_build.profile.get("seed_strategy"),
                "mode": "gene_set_collection",
                "query_gene_set_path": str(paths["query_gene_set"]),
                "gene_set_path": str(paths["gene_set"]),
                "query_term_count": len(query_terms),
                "target_term_count": len(target_terms),
                "total_pairs": pair_count,
                "returned_rows": len(rows),
                "id_mapping": request.id_mapping,
                "cache": cache_build.profile,
                "timing_seconds": self._timing_profile(
                    cache_build.profile, scoring_seconds, total_seconds
                ),
            },
        )
        self._write_collection_downloads(
            artifact_dir=artifact_dir,
            result=result,
            query_terms=query_terms,
            target_terms=target_terms,
            query_names=query_names,
            target_names=target_names,
            query_indices=query_indices_np,
            target_indices=target_indices_np,
            zscores=np.asarray(zscores, dtype=np.float64),
            p_values=p_values.reshape((len(query_terms), len(target_terms))),
            p_corrected=p_corrected.reshape((len(query_terms), len(target_terms))),
        )
        return result

    def run_gsea(self, request: GseaRequest, artifact_dir: Path | None = None) -> AnalysisResult:
        total_start = time.perf_counter()
        paths = self._resolve_paths(request)
        func = self.legacy.func_optimized
        gsea = self.legacy.func_gsea
        ld = self.legacy.load_data
        workers = request.workers or self.settings.workers
        seed = request.seed if request.seed is not None else self.settings.seed
        ite = request.null_iterations or self.settings.null_iterations

        e_unit, node_list = load_embedding(paths["embedding"], paths["gene_list"], func)
        node_set = set(node_list)
        node2index = {gene: i for i, gene in enumerate(node_list)}

        ranked = sorted(request.ranked_genes, key=lambda row: row[1], reverse=True)
        ranked_genes = [gene for gene, _score in ranked]
        valid_ranked = [gene for gene in ranked_genes if gene in node2index]
        invalid_genes = [gene for gene in ranked_genes if gene not in node2index]
        gene_counts = self._gene_counts_from_mapping(
            request.id_mapping.get("genes"),
            input_count=len(ranked_genes),
            matched_count=len(valid_ranked),
            unmapped=invalid_genes,
        )
        if not valid_ranked:
            raise ValueError("none of the ranked genes are present in the embedding gene list")

        ranked_idx = np.asarray([node2index[gene] for gene in valid_ranked], dtype=np.int32)
        ranked_emb = gsea.compute_ranked_emb(e_unit, ranked_idx)
        ranked_emb_t = np.ascontiguousarray(ranked_emb.T, dtype=np.float32)

        gene_sets = ld.load_gmt(str(paths["gene_set"]))
        term_names = ld.term2name(str(paths["gene_set"]))
        geneset_indices = ld.term2indexes(
            gene_sets,
            node2index,
            upper=request.max_gene_set_size,
            lower=request.min_gene_set_size,
        )
        geneset_indices_np = func.preconvert_indices_to_arrays(geneset_indices)
        geneset_terms = sorted(geneset_indices_np.keys())
        if not geneset_terms:
            raise ValueError("no gene-set terms survived the size filters")
        self._enforce_pair_limit(len(geneset_terms))

        all_bg_genes = set().union(*gene_sets.values()) & node_set
        pop = np.asarray(sorted(node2index[gene] for gene in all_bg_genes), dtype=np.int32)
        sizes = {len(geneset_indices_np[term]) for term in geneset_terms}
        cache_build = self._load_or_build_es_cache(
            gsea, e_unit, pop, ranked_emb, sizes, ite, seed, workers
        )
        cache = cache_build.cache

        scoring_start = time.perf_counter()
        true_scores, z_scores = gsea.score_terms_batched(
            e_unit,
            geneset_indices_np,
            geneset_terms,
            ranked_emb_t,
            cache,
        )
        scoring_seconds = time.perf_counter() - scoring_start
        total_seconds = time.perf_counter() - total_start
        z = np.asarray([z_scores[term] for term in geneset_terms], dtype=np.float64)
        p_values = 2 * (1 - norm.cdf(np.abs(z)))
        p_corrected = multipletests(p_values, method="fdr_bh")[1]
        rows = self._result_rows(
            terms=geneset_terms,
            term_names=term_names,
            sizes=[len(geneset_indices_np[term]) for term in geneset_terms],
            z_scores=z,
            p_values=p_values,
            p_corrected=p_corrected,
            true_scores=[float(true_scores[term]) for term in geneset_terms],
        )

        result = AnalysisResult(
            kind=AnalysisKind.GSEA,
            results=rows,
            input_gene_count=gene_counts.input_count,
            valid_gene_count=gene_counts.matched_count,
            invalid_genes=gene_counts.unmapped,
            warnings=self._warnings(gene_counts.unmapped),
            parameters={
                "min_gene_set_size": request.min_gene_set_size,
                "max_gene_set_size": request.max_gene_set_size,
                "null_iterations": ite,
                "workers": workers,
                "seed": cache_build.profile.get("seed"),
                "seed_strategy": cache_build.profile.get("seed_strategy"),
                "gene_set_path": str(paths["gene_set"]),
                "target_term_count": len(geneset_terms),
                "total_pairs": len(geneset_terms),
                "id_mapping": request.id_mapping,
                "cache": cache_build.profile,
                "timing_seconds": self._timing_profile(
                    cache_build.profile, scoring_seconds, total_seconds
                ),
            },
        )
        self._write_result_downloads(artifact_dir, result)
        return result

    def preview_set_similarity(self, request: SetSimilarityRequest) -> dict[str, object]:
        paths = self._resolve_paths(request)
        func = self.legacy.func_optimized
        ld = self.legacy.load_data
        seed = request.seed if request.seed is not None else self.settings.seed
        ite = request.null_iterations or self.settings.null_iterations
        e_unit, node_list = load_embedding(paths["embedding"], paths["gene_list"], func)
        node_set = set(node_list)
        node2index = {gene: i for i, gene in enumerate(node_list)}

        target_sets = ld.load_gmt(str(paths["gene_set"]))
        target_indices = ld.term2indexes(
            target_sets,
            node2index,
            upper=request.max_gene_set_size,
            lower=request.min_gene_set_size,
        )
        target_indices_np = func.preconvert_indices_to_arrays(target_indices)
        target_terms = sorted(target_indices_np.keys())
        if not target_terms:
            raise ValueError("no target gene-set terms survived the size filters")

        if request.query_gene_set_path is None:
            if request.genes is None:
                raise ValueError("input genes are required")
            input_genes = clean_gene_list(request.genes)
            valid_genes = [gene for gene in input_genes if gene in node2index]
            invalid_genes = [gene for gene in input_genes if gene not in node2index]
            gene_counts = self._gene_counts_from_mapping(
                request.id_mapping.get("genes"),
                input_count=len(input_genes),
                matched_count=len(valid_genes),
                unmapped=invalid_genes,
            )
            if not valid_genes:
                raise ValueError("none of the input genes are present in the embedding gene list")
            pop = np.asarray(
                func.get_background_indices(target_sets, node_set, node2index), dtype=np.int32
            )
            size_pairs = {(len(valid_genes), len(target_indices_np[term])) for term in target_terms}
            pair_count = len(target_terms)
            cache = self._inspect_bma_cache(func, e_unit, pop, pop, size_pairs, ite, seed)
            return {
                "kind": AnalysisKind.SET_SIMILARITY.value,
                "mode": "gene_list",
                "can_submit": self._can_submit_pair_count(pair_count),
                "over_limit": pair_count > self.settings.max_term_pairs,
                "max_term_pairs": self.settings.max_term_pairs,
                "estimated_pair_count": pair_count,
                "genes": {
                    "input_count": gene_counts.input_count,
                    "matched_count": gene_counts.matched_count,
                    "unmatched_count": len(gene_counts.unmapped),
                    "unmatched_examples": gene_counts.unmapped[:10],
                    "id_type_counts": gene_counts.id_type_counts,
                },
                "target_collection": self._collection_summary(target_sets, target_indices_np),
                "cache": cache,
                "warnings": self._limit_warnings(pair_count),
            }

        query_sets = ld.load_gmt(str(paths["query_gene_set"]))
        query_indices = ld.term2indexes(
            query_sets,
            node2index,
            upper=request.max_gene_set_size,
            lower=request.min_gene_set_size,
        )
        query_indices_np = func.preconvert_indices_to_arrays(query_indices)
        query_terms = sorted(query_indices_np.keys())
        if not query_terms:
            raise ValueError("no query gene-set terms survived the size filters")
        pair_count = len(query_terms) * len(target_terms)
        bg1 = np.asarray(
            sorted(func.get_background_indices(query_sets, node_set, node2index)), dtype=np.int32
        )
        bg2 = np.asarray(
            sorted(func.get_background_indices(target_sets, node_set, node2index)), dtype=np.int32
        )
        size_pairs = {
            (len(query_indices_np[query]), len(target_indices_np[target]))
            for query in query_terms
            for target in target_terms
        }
        cache = self._inspect_bma_cache(func, e_unit, bg1, bg2, size_pairs, ite, seed)
        return {
            "kind": AnalysisKind.SET_SIMILARITY.value,
            "mode": "gene_set_collection",
            "can_submit": self._can_submit_pair_count(pair_count),
            "over_limit": pair_count > self.settings.max_term_pairs,
            "max_term_pairs": self.settings.max_term_pairs,
            "estimated_pair_count": pair_count,
            "query_collection": self._collection_summary(query_sets, query_indices_np),
            "target_collection": self._collection_summary(target_sets, target_indices_np),
            "cache": cache,
            "warnings": self._limit_warnings(pair_count),
        }

    def preview_gsea(self, request: GseaRequest) -> dict[str, object]:
        paths = self._resolve_paths(request)
        func = self.legacy.func_optimized
        gsea = self.legacy.func_gsea
        ld = self.legacy.load_data
        seed = request.seed if request.seed is not None else self.settings.seed
        ite = request.null_iterations or self.settings.null_iterations
        e_unit, node_list = load_embedding(paths["embedding"], paths["gene_list"], func)
        node_set = set(node_list)
        node2index = {gene: i for i, gene in enumerate(node_list)}

        ranked = sorted(request.ranked_genes, key=lambda row: row[1], reverse=True)
        ranked_genes = [gene for gene, _score in ranked]
        valid_ranked = [gene for gene in ranked_genes if gene in node2index]
        invalid_genes = [gene for gene in ranked_genes if gene not in node2index]
        gene_counts = self._gene_counts_from_mapping(
            request.id_mapping.get("genes"),
            input_count=len(ranked_genes),
            matched_count=len(valid_ranked),
            unmapped=invalid_genes,
        )
        if not valid_ranked:
            raise ValueError("none of the ranked genes are present in the embedding gene list")

        gene_sets = ld.load_gmt(str(paths["gene_set"]))
        geneset_indices = ld.term2indexes(
            gene_sets,
            node2index,
            upper=request.max_gene_set_size,
            lower=request.min_gene_set_size,
        )
        geneset_indices_np = func.preconvert_indices_to_arrays(geneset_indices)
        geneset_terms = sorted(geneset_indices_np.keys())
        if not geneset_terms:
            raise ValueError("no gene-set terms survived the size filters")
        pair_count = len(geneset_terms)
        all_bg_genes = set().union(*gene_sets.values()) & node_set
        pop = np.asarray(sorted(node2index[gene] for gene in all_bg_genes), dtype=np.int32)
        ranked_idx = np.asarray([node2index[gene] for gene in valid_ranked], dtype=np.int32)
        ranked_emb = gsea.compute_ranked_emb(e_unit, ranked_idx)
        sizes = {len(geneset_indices_np[term]) for term in geneset_terms}
        cache = self._inspect_es_cache(gsea, e_unit, pop, ranked_emb, sizes, ite, seed)
        return {
            "kind": AnalysisKind.GSEA.value,
            "mode": "ranked_enrichment",
            "can_submit": self._can_submit_pair_count(pair_count),
            "over_limit": pair_count > self.settings.max_term_pairs,
            "max_term_pairs": self.settings.max_term_pairs,
            "estimated_pair_count": pair_count,
            "genes": {
                "input_count": gene_counts.input_count,
                "matched_count": gene_counts.matched_count,
                "unmatched_count": len(gene_counts.unmapped),
                "unmatched_examples": gene_counts.unmapped[:10],
                "id_type_counts": gene_counts.id_type_counts,
            },
            "target_collection": self._collection_summary(gene_sets, geneset_indices_np),
            "cache": cache,
            "warnings": self._limit_warnings(pair_count),
        }

    def _resolve_paths(self, request) -> dict[str, Path]:
        paths = {
            "embedding": request.embedding_path or self.settings.embedding_path,
            "gene_list": request.gene_list_path or self.settings.gene_list_path,
            "gene_set": request.gene_set_path or self.settings.default_gene_set_path,
        }
        if getattr(request, "query_gene_set_path", None) is not None:
            paths["query_gene_set"] = request.query_gene_set_path
        for name, path in paths.items():
            path = Path(path).expanduser().resolve()
            if not path.exists():
                raise FileNotFoundError(f"{name} path does not exist: {path}")
            paths[name] = path
        if request.max_gene_set_size < request.min_gene_set_size:
            raise ValueError("max_gene_set_size must be >= min_gene_set_size")
        return paths

    def _load_or_build_bma_cache(
        self, func, e_unit, pop1, pop2, size_pairs, ite, seed, workers
    ) -> CacheBuild:
        started = time.perf_counter()
        requested_pairs = sorted(size_pairs)
        cache_dir = self.settings.cache_dir.expanduser().resolve() / "bma"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = Path(func.NullCacheBMA.suggest_path(str(cache_dir), e_unit, pop1, pop2))
        seed_resolution = self._resolve_cache_seed(
            seed,
            kind="bma",
            cache_path=cache_path,
            ite=ite,
            resolver=func.NullCacheBMA.resolve_seed,
        )
        seed = seed_resolution.seed
        with cache_file_lock(cache_path):
            cache = func.NullCacheBMA()
            existed = cache_path.exists()
            expected = func.NullCacheBMA.build_metadata(
                e_unit,
                pop1,
                pop2,
                ite,
                seed,
                null_sampling="prefix_coupled",
            )
            metadata_ok = False
            reason = ""
            missing: list[tuple[int, int]] = requested_pairs
            status = "build"
            if existed:
                cache.load(str(cache_path))
                metadata_ok, reason = cache.metadata_matches(expected)
                missing = [pair for pair in size_pairs if pair not in cache.cache]
                if metadata_ok and not missing:
                    cache_path.touch()
                    return CacheBuild(
                        cache=cache,
                        profile=self._cache_profile(
                            kind="bma",
                            status="reuse",
                            hit=True,
                            cache_path=cache_path,
                            requested_count=len(requested_pairs),
                            added_count=0,
                            elapsed=time.perf_counter() - started,
                            metadata_ok=metadata_ok,
                            reason=reason,
                            seed=seed,
                            seed_strategy=seed_resolution.strategy,
                        ),
                    )
                status = "extend_or_rebuild"
                if not metadata_ok:
                    cache = func.NullCacheBMA()
                    missing = requested_pairs

            if workers > 1 and hasattr(cache, "precompute_prefix_parallel"):
                cache.precompute_prefix_parallel(
                    e_unit,
                    pop1,
                    missing,
                    ite=ite,
                    seed=seed,
                    verbose=False,
                    population_idx2=pop2,
                )
            else:
                cache.precompute_prefix(
                    e_unit,
                    pop1,
                    missing,
                    ite=ite,
                    seed=seed,
                    verbose=False,
                    population_idx2=pop2,
                )
            cache.save(str(cache_path))
            return CacheBuild(
                cache=cache,
                profile=self._cache_profile(
                    kind="bma",
                    status=status,
                    hit=False,
                    cache_path=cache_path,
                    requested_count=len(requested_pairs),
                    added_count=len(missing),
                    elapsed=time.perf_counter() - started,
                    metadata_ok=metadata_ok if existed else None,
                    reason=reason,
                    seed=seed,
                    seed_strategy=seed_resolution.strategy,
                ),
            )

    def _load_or_build_es_cache(self, gsea, e_unit, pop, ranked_emb, sizes, ite, seed, workers):
        started = time.perf_counter()
        requested_sizes = sorted(sizes)
        cache_dir = self.settings.cache_dir.expanduser().resolve() / "es"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = Path(
            gsea.NullCacheESBetter.suggest_path(str(cache_dir), e_unit, pop, ranked_emb)
        )
        seed_resolution = self._resolve_cache_seed(
            seed,
            kind="es",
            cache_path=cache_path,
            ite=ite,
            resolver=gsea.NullCacheESBetter.resolve_seed,
        )
        seed = seed_resolution.seed
        with cache_file_lock(cache_path):
            expected = gsea.NullCacheESBetter.build_metadata(e_unit, pop, ranked_emb, ite, seed)
            existed = cache_path.exists()
            metadata_ok = False
            reason = ""
            missing = requested_sizes
            status = "build"
            if existed:
                cache = gsea.NullCacheESBetter.load(str(cache_path))
                metadata_ok, reason = cache.metadata_matches(expected)
                missing = cache.missing_sizes(sizes) if metadata_ok else requested_sizes
                if metadata_ok and not missing:
                    cache_path.touch()
                    return CacheBuild(
                        cache=cache,
                        profile=self._cache_profile(
                            kind="es",
                            status="reuse",
                            hit=True,
                            cache_path=cache_path,
                            requested_count=len(requested_sizes),
                            added_count=0,
                            elapsed=time.perf_counter() - started,
                            metadata_ok=metadata_ok,
                            reason=reason,
                            seed=seed,
                            seed_strategy=seed_resolution.strategy,
                        ),
                    )
                status = "extend_or_rebuild"
            else:
                cache = gsea.NullCacheESBetter()

            if existed and not metadata_ok:
                cache = gsea.NullCacheESBetter()
            if workers > 1:
                cache.precompute_parallel(
                    e_unit,
                    pop,
                    missing,
                    ranked_emb,
                    ite=ite,
                    seed=seed,
                    verbose=False,
                    n_workers=workers,
                )
            else:
                cache.precompute(
                    e_unit, pop, missing, ranked_emb, ite=ite, seed=seed, verbose=False
                )
            cache.save(str(cache_path))
            return CacheBuild(
                cache=cache,
                profile=self._cache_profile(
                    kind="es",
                    status=status,
                    hit=False,
                    cache_path=cache_path,
                    requested_count=len(requested_sizes),
                    added_count=len(missing),
                    elapsed=time.perf_counter() - started,
                    metadata_ok=metadata_ok if existed else None,
                    reason=reason,
                    seed=seed,
                    seed_strategy=seed_resolution.strategy,
                ),
            )

    def _inspect_bma_cache(
        self, func, e_unit, pop1, pop2, size_pairs, ite, seed
    ) -> dict[str, object]:
        requested_pairs = sorted(size_pairs)
        cache_dir = self.settings.cache_dir.expanduser().resolve() / "bma"
        cache_path = Path(func.NullCacheBMA.suggest_path(str(cache_dir), e_unit, pop1, pop2))
        seed_resolution = self._resolve_cache_seed(
            seed,
            kind="bma",
            cache_path=cache_path,
            ite=ite,
            resolver=func.NullCacheBMA.resolve_seed,
        )
        with cache_file_lock(cache_path):
            if not cache_path.exists():
                return {
                    "kind": "bma",
                    "status": "build",
                    "hit": False,
                    "path": str(cache_path),
                    "file": cache_path.name,
                    "seed": seed_resolution.seed,
                    "seed_strategy": seed_resolution.strategy,
                    "requested_size_pairs": len(requested_pairs),
                    "missing_size_pairs": len(requested_pairs),
                    "added_size_pairs": len(requested_pairs),
                }
            cache = func.NullCacheBMA()
            cache.load(str(cache_path))
            expected = func.NullCacheBMA.build_metadata(
                e_unit,
                pop1,
                pop2,
                ite,
                seed_resolution.seed,
                null_sampling="prefix_coupled",
            )
            metadata_ok, reason = cache.metadata_matches(expected)
            missing = (
                [pair for pair in size_pairs if pair not in cache.cache]
                if metadata_ok
                else list(size_pairs)
            )
            return {
                "kind": "bma",
                "status": "reuse" if metadata_ok and not missing else "extend_or_rebuild",
                "hit": metadata_ok and not missing,
                "path": str(cache_path),
                "file": cache_path.name,
                "seed": seed_resolution.seed,
                "seed_strategy": seed_resolution.strategy,
                "metadata_ok": metadata_ok,
                "reason": reason,
                "requested_size_pairs": len(requested_pairs),
                "missing_size_pairs": len(missing),
                "added_size_pairs": len(missing),
            }

    def _inspect_es_cache(
        self, gsea, e_unit, pop, ranked_emb, sizes, ite, seed
    ) -> dict[str, object]:
        requested_sizes = sorted(sizes)
        cache_dir = self.settings.cache_dir.expanduser().resolve() / "es"
        cache_path = Path(
            gsea.NullCacheESBetter.suggest_path(str(cache_dir), e_unit, pop, ranked_emb)
        )
        seed_resolution = self._resolve_cache_seed(
            seed,
            kind="es",
            cache_path=cache_path,
            ite=ite,
            resolver=gsea.NullCacheESBetter.resolve_seed,
        )
        with cache_file_lock(cache_path):
            if not cache_path.exists():
                return {
                    "kind": "es",
                    "status": "build",
                    "hit": False,
                    "path": str(cache_path),
                    "file": cache_path.name,
                    "seed": seed_resolution.seed,
                    "seed_strategy": seed_resolution.strategy,
                    "requested_sizes": len(requested_sizes),
                    "missing_sizes": len(requested_sizes),
                    "added_sizes": len(requested_sizes),
                }
            cache = gsea.NullCacheESBetter.load(str(cache_path))
            expected = gsea.NullCacheESBetter.build_metadata(
                e_unit,
                pop,
                ranked_emb,
                ite,
                seed_resolution.seed,
            )
            metadata_ok, reason = cache.metadata_matches(expected)
            missing = cache.missing_sizes(sizes) if metadata_ok else list(sizes)
            return {
                "kind": "es",
                "status": "reuse" if metadata_ok and not missing else "extend_or_rebuild",
                "hit": metadata_ok and not missing,
                "path": str(cache_path),
                "file": cache_path.name,
                "seed": seed_resolution.seed,
                "seed_strategy": seed_resolution.strategy,
                "metadata_ok": metadata_ok,
                "reason": reason,
                "requested_sizes": len(requested_sizes),
                "missing_sizes": len(missing),
                "added_sizes": len(missing),
            }

    def _cache_profile(
        self,
        *,
        kind: str,
        status: str,
        hit: bool,
        cache_path: Path,
        requested_count: int,
        added_count: int,
        elapsed: float,
        metadata_ok: bool | None,
        reason: str,
        seed: int,
        seed_strategy: str,
    ) -> dict[str, object]:
        key = "size_pairs" if kind == "bma" else "sizes"
        return {
            "kind": kind,
            "status": status,
            "hit": hit,
            "path": str(cache_path),
            "file": cache_path.name,
            "seed": seed,
            "seed_strategy": seed_strategy,
            f"requested_{key}": requested_count,
            f"added_{key}": added_count,
            f"missing_{key}": added_count,
            "metadata_ok": metadata_ok,
            "reason": reason,
            "cache_seconds": elapsed,
        }

    def _resolve_cache_seed(
        self,
        seed: int | None,
        *,
        kind: str,
        cache_path: Path,
        ite: int,
        resolver,
    ) -> SeedResolution:
        if seed is not None:
            return SeedResolution(seed=int(resolver(seed)), strategy="configured")
        payload = f"andes-v2-null-seed-v1:{kind}:{cache_path.name}:ite={ite}".encode()
        derived = int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "big")
        return SeedResolution(seed=derived, strategy="cache_key")

    def _timing_profile(
        self,
        cache_profile: dict[str, object],
        scoring_seconds: float,
        total_seconds: float,
    ) -> dict[str, float]:
        cache_seconds = cache_profile.get("cache_seconds", 0.0)
        return {
            "cache": float(cache_seconds) if isinstance(cache_seconds, (int, float)) else 0.0,
            "scoring": scoring_seconds,
            "total": total_seconds,
        }

    def _collection_summary(self, raw_sets: dict, filtered_indices: dict) -> dict[str, object]:
        genes = set().union(*raw_sets.values()) if raw_sets else set()
        filtered_genes = set()
        size_values: list[int] = []
        for indices in filtered_indices.values():
            filtered_genes.update(indices)
            size_values.append(len(indices))
        return {
            "term_count": len(raw_sets),
            "usable_term_count": len(filtered_indices),
            "gene_count": len(genes),
            "matched_gene_count": len(filtered_genes),
            "min_usable_size": min(size_values) if size_values else None,
            "max_usable_size": max(size_values) if size_values else None,
        }

    def _gene_counts_from_mapping(
        self,
        mapping: object,
        *,
        input_count: int,
        matched_count: int,
        unmapped: list[str],
    ) -> GeneCounts:
        if isinstance(mapping, dict):
            records = mapping.get("records")
            mapped_count = mapping.get("mapped_count", matched_count)
            unmapped_examples = mapping.get("unmapped_examples", unmapped)
            id_type_counts = mapping.get("id_type_counts", {})
            return GeneCounts(
                input_count=len(records) if isinstance(records, list) else input_count,
                matched_count=int(mapped_count),
                unmapped=list(unmapped_examples)
                if isinstance(unmapped_examples, list)
                else unmapped,
                id_type_counts=id_type_counts if isinstance(id_type_counts, dict) else {},
            )
        return GeneCounts(
            input_count=input_count,
            matched_count=matched_count,
            unmapped=unmapped,
            id_type_counts={},
        )

    def _enforce_pair_limit(self, pair_count: int) -> None:
        if pair_count > self.settings.max_term_pairs and not self.settings.allow_large_jobs:
            raise ValueError(
                f"job would score {pair_count} pair(s), above ANDES_MAX_TERM_PAIRS="
                f"{self.settings.max_term_pairs}"
            )

    def _can_submit_pair_count(self, pair_count: int) -> bool:
        return pair_count <= self.settings.max_term_pairs or self.settings.allow_large_jobs

    def _limit_warnings(self, pair_count: int) -> list[str]:
        if pair_count <= self.settings.max_term_pairs:
            return []
        if self.settings.allow_large_jobs:
            return [
                f"Estimated {pair_count} pairs exceeds server limit "
                f"{self.settings.max_term_pairs}; admin override is enabled."
            ]
        return [
            f"Estimated {pair_count} pairs exceeds server limit "
            f"{self.settings.max_term_pairs}; this job cannot be queued."
        ]

    def _write_result_downloads(self, artifact_dir: Path | None, result: AnalysisResult) -> None:
        if artifact_dir is None:
            return
        artifact_dir.mkdir(parents=True, exist_ok=True)
        csv_path = artifact_dir / "results.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "term",
                    "description",
                    "size",
                    "true_score",
                    "z_score",
                    "p_value",
                    "p_value_corrected",
                    "log10_p_value_corrected",
                    "significant",
                ]
            )
            for row in result.results:
                writer.writerow(
                    [
                        row.term,
                        row.description or "",
                        row.size or "",
                        row.true_score if row.true_score is not None else "",
                        row.z_score,
                        row.p_value,
                        row.p_value_corrected,
                        row.log10_p_value_corrected,
                        row.significant,
                    ]
                )

    def _write_collection_downloads(
        self,
        *,
        artifact_dir: Path | None,
        result: AnalysisResult,
        query_terms: list[str],
        target_terms: list[str],
        query_names: dict[str, str],
        target_names: dict[str, str],
        query_indices: dict,
        target_indices: dict,
        zscores: np.ndarray,
        p_values: np.ndarray,
        p_corrected: np.ndarray,
    ) -> None:
        if artifact_dir is None:
            return
        artifact_dir.mkdir(parents=True, exist_ok=True)
        pair_path = artifact_dir / "pair-table.csv"
        with pair_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "query_term",
                    "query_description",
                    "query_size",
                    "target_term",
                    "target_description",
                    "target_size",
                    "z_score",
                    "p_value",
                    "p_value_corrected",
                ]
            )
            for query_idx, query_term in enumerate(query_terms):
                for target_idx, target_term in enumerate(target_terms):
                    writer.writerow(
                        [
                            query_term,
                            query_names.get(query_term, ""),
                            len(query_indices[query_term]),
                            target_term,
                            target_names.get(target_term, ""),
                            len(target_indices[target_term]),
                            float(zscores[query_idx, target_idx]),
                            float(p_values[query_idx, target_idx]),
                            float(p_corrected[query_idx, target_idx]),
                        ]
                    )

        matrix_path = artifact_dir / "matrix.csv"
        with matrix_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["query_term", *target_terms])
            for query_idx, query_term in enumerate(query_terms):
                writer.writerow(
                    [
                        query_term,
                        *[
                            float(zscores[query_idx, target_idx])
                            for target_idx in range(len(target_terms))
                        ],
                    ]
                )
        self._write_result_downloads(artifact_dir, result)

    def _result_rows(
        self,
        terms: list[str],
        term_names: dict[str, str],
        sizes: list[int],
        z_scores: np.ndarray,
        p_values: np.ndarray,
        p_corrected: np.ndarray,
        true_scores: list[float] | None,
    ) -> list[ResultTerm]:
        rows: list[ResultTerm] = []
        for idx, term in enumerate(terms):
            corrected = float(p_corrected[idx])
            log10_p = -math.log10(corrected) if corrected > 0 else -math.log10(np.finfo(float).tiny)
            rows.append(
                ResultTerm(
                    term=term,
                    description=term_names.get(term),
                    size=int(sizes[idx]),
                    true_score=true_scores[idx] if true_scores is not None else None,
                    z_score=float(z_scores[idx]),
                    p_value=float(p_values[idx]),
                    p_value_corrected=corrected,
                    log10_p_value_corrected=float(log10_p),
                    significant=corrected < 0.05,
                )
            )
        return sorted(rows, key=lambda row: row.z_score, reverse=True)

    def _warnings(self, invalid_genes: list[str]) -> list[str]:
        if not invalid_genes:
            return []
        return [f"{len(invalid_genes)} gene(s) were not found in the embedding and were excluded."]
