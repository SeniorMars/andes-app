from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CachePruneResult:
    scanned_files: int
    deleted_files: int
    kept_files: int
    deleted_bytes: int
    dry_run: bool


def prune_cache(
    cache_dir: Path,
    *,
    max_age_days: int,
    min_keep_files: int,
    max_bytes: int | None = None,
    dry_run: bool = False,
    now: float | None = None,
) -> CachePruneResult:
    root = cache_dir.expanduser().resolve()
    if not root.exists():
        return CachePruneResult(0, 0, 0, 0, dry_run)

    timestamp = time.time() if now is None else now
    cutoff = timestamp - (max_age_days * 86400)
    files = [path for path in root.rglob("*.pkl") if path.is_file()]
    by_recent = sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)
    protected = set(by_recent[: max(0, min_keep_files)])

    delete_candidates: list[Path] = [
        path for path in by_recent if path not in protected and path.stat().st_mtime < cutoff
    ]

    if max_bytes is not None and max_bytes > 0:
        candidate_set = set(delete_candidates)
        remaining_bytes = sum(path.stat().st_size for path in files)
        for path in reversed(by_recent):
            if remaining_bytes <= max_bytes:
                break
            if path in protected or path in candidate_set:
                continue
            delete_candidates.append(path)
            candidate_set.add(path)
            remaining_bytes -= path.stat().st_size

    deleted_bytes = sum(path.stat().st_size for path in delete_candidates if path.exists())
    if not dry_run:
        for path in delete_candidates:
            path.unlink(missing_ok=True)

    deleted_files = len(delete_candidates)
    return CachePruneResult(
        scanned_files=len(files),
        deleted_files=deleted_files,
        kept_files=len(files) - deleted_files,
        deleted_bytes=deleted_bytes,
        dry_run=dry_run,
    )
