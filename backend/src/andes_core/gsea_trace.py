from __future__ import annotations

import numpy as np

from .schemas import ResultTerm


def sample_trace_indices(
    length: int,
    max_points: int,
    *,
    required_index: int,
) -> list[int]:
    if length <= 0 or max_points <= 0:
        return []
    required = min(length - 1, max(0, required_index))
    if max_points == 1:
        return [required]
    if length <= max_points:
        return list(range(length))
    count = max(2, max_points)
    sampled = {
        min(length - 1, max(0, round(index * (length - 1) / (count - 1))))
        for index in range(count)
    }
    sampled.update({0, length - 1, required})
    while len(sampled) > max_points:
        removable = sorted(index for index in sampled if index not in {0, length - 1, required})
        if not removable:
            for endpoint in (0, length - 1):
                if endpoint != required and endpoint in sampled:
                    sampled.remove(endpoint)
                    break
            else:
                break
        else:
            sampled.remove(removable[len(removable) // 2])
    return sorted(sampled)


def compute_gsea_es_trace(
    *,
    e_unit: np.ndarray,
    gene_set_idx: np.ndarray,
    ranked_emb: np.ndarray,
    query_memory_mb: float,
) -> dict[str, np.ndarray | int | float]:
    gene_set_idx = np.asarray(gene_set_idx, dtype=np.int32)
    ranked_emb_t = np.ascontiguousarray(ranked_emb.T, dtype=np.float32)
    ranked_count = ranked_emb.shape[0]
    budget_bytes = max(1, int(float(query_memory_mb) * 1024 * 1024))
    bytes_per_term_row = max(1, ranked_count * np.dtype(np.float32).itemsize)
    rows_per_block = max(
        1,
        min(len(gene_set_idx), budget_bytes // bytes_per_term_row),
    )
    columns = np.arange(ranked_count)
    best_gene_set_position = np.zeros(ranked_count, dtype=np.int32)
    best_match_score = np.full(ranked_count, -np.inf, dtype=np.float32)
    for start in range(0, len(gene_set_idx), rows_per_block):
        stop = min(len(gene_set_idx), start + rows_per_block)
        block_emb = e_unit[gene_set_idx[start:stop]]
        similarities = block_emb @ ranked_emb_t
        local_position = similarities.argmax(axis=0).astype(np.int32)
        local_score = similarities[local_position, columns].astype(np.float32)
        better = local_score > best_match_score
        best_match_score[better] = local_score[better]
        best_gene_set_position[better] = start + local_position[better]
    centered_score = (best_match_score - best_match_score.mean()).astype(np.float32)
    running_es = np.cumsum(centered_score, dtype=np.float32)
    es_index = int(np.abs(running_es).argmax())
    return {
        "best_match_score": best_match_score,
        "best_gene_set_position": best_gene_set_position,
        "centered_score": centered_score,
        "running_es": running_es,
        "es_index": es_index,
        "es": float(running_es[es_index]),
    }


def gsea_trace_payload(
    *,
    e_unit: np.ndarray,
    geneset_indices: dict[str, np.ndarray],
    node_list: list[str],
    ranked_emb: np.ndarray,
    ranked_genes: list[tuple[str, float]],
    rows: list[ResultTerm],
    term_names: dict[str, str],
    query_memory_mb: float,
    max_terms: int = 5,
    max_points: int = 600,
) -> dict[str, object] | None:
    if len(ranked_genes) < 2 or not rows:
        return None
    selected_rows = sorted(
        rows,
        key=lambda row: (row.p_value_corrected, -abs(row.z_score), row.term),
    )[:max_terms]
    terms: list[dict[str, object]] = []
    ranked_ids = [gene for gene, _score in ranked_genes]
    ranked_scores = [float(score) for _gene, score in ranked_genes]
    for row in selected_rows:
        term_indices = geneset_indices.get(row.term)
        if term_indices is None or len(term_indices) == 0:
            continue
        trace = compute_gsea_es_trace(
            e_unit=e_unit,
            gene_set_idx=term_indices,
            ranked_emb=ranked_emb,
            query_memory_mb=query_memory_mb,
        )
        sample_indices = sample_trace_indices(
            len(ranked_genes),
            max_points,
            required_index=int(trace["es_index"]),
        )
        best_positions = np.asarray(trace["best_gene_set_position"], dtype=np.int32)
        best_match_score = np.asarray(trace["best_match_score"], dtype=np.float32)
        centered_score = np.asarray(trace["centered_score"], dtype=np.float32)
        running_es = np.asarray(trace["running_es"], dtype=np.float32)
        points: list[dict[str, object]] = []
        for index in sample_indices:
            best_gene_idx = int(term_indices[int(best_positions[index])])
            points.append(
                {
                    "rank": index + 1,
                    "gene": ranked_ids[index],
                    "rank_score": ranked_scores[index],
                    "best_match_gene": node_list[best_gene_idx],
                    "match_score": float(best_match_score[index]),
                    "centered_score": float(centered_score[index]),
                    "running_es": float(running_es[index]),
                }
            )
        terms.append(
            {
                "term": row.term,
                "description": term_names.get(row.term),
                "size": row.size,
                "true_score": row.true_score,
                "z_score": row.z_score,
                "p_value_corrected": row.p_value_corrected,
                "es": float(trace["es"]),
                "es_rank": int(trace["es_index"]) + 1,
                "sampled": len(sample_indices) < len(ranked_genes),
                "points": points,
            }
        )
    if not terms:
        return None
    return {
        "algorithm": "andes_best_match_trace_v1",
        "exact": True,
        "ranked_gene_count": len(ranked_genes),
        "max_points_per_term": max_points,
        "terms": terms,
    }
