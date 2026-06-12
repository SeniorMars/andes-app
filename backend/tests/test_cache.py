from __future__ import annotations

import os

from andes_core.cache import prune_cache


def _write_cache_file(path, *, size: int, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    os.utime(path, (mtime, mtime))


def test_prune_cache_deletes_old_unprotected_files(tmp_path):
    now = 1_000_000.0
    old_file = tmp_path / "bma" / "old.pkl"
    recent_file = tmp_path / "bma" / "recent.pkl"
    _write_cache_file(old_file, size=4, mtime=now - 40 * 86400)
    _write_cache_file(recent_file, size=4, mtime=now - 1 * 86400)

    result = prune_cache(tmp_path, max_age_days=30, min_keep_files=1, now=now)

    assert result.scanned_files == 2
    assert result.deleted_files == 1
    assert result.deleted_bytes == 4
    assert not old_file.exists()
    assert recent_file.exists()


def test_prune_cache_dry_run_keeps_files(tmp_path):
    now = 1_000_000.0
    old_file = tmp_path / "es" / "old.pkl"
    _write_cache_file(old_file, size=4, mtime=now - 40 * 86400)

    result = prune_cache(tmp_path, max_age_days=30, min_keep_files=0, dry_run=True, now=now)

    assert result.deleted_files == 1
    assert result.dry_run is True
    assert old_file.exists()


def test_prune_cache_min_keep_protects_recent_files(tmp_path):
    now = 1_000_000.0
    files = []
    for idx in range(3):
        path = tmp_path / "bma" / f"cache-{idx}.pkl"
        _write_cache_file(path, size=4, mtime=now - (idx + 40) * 86400)
        files.append(path)

    result = prune_cache(tmp_path, max_age_days=30, min_keep_files=2, now=now)

    assert result.deleted_files == 1
    assert files[0].exists()
    assert files[1].exists()
    assert not files[2].exists()


def test_prune_cache_can_trim_to_byte_budget(tmp_path):
    now = 1_000_000.0
    newest = tmp_path / "bma" / "newest.pkl"
    middle = tmp_path / "bma" / "middle.pkl"
    oldest = tmp_path / "bma" / "oldest.pkl"
    _write_cache_file(newest, size=10, mtime=now)
    _write_cache_file(middle, size=10, mtime=now - 1)
    _write_cache_file(oldest, size=10, mtime=now - 2)

    result = prune_cache(
        tmp_path,
        max_age_days=30,
        min_keep_files=1,
        max_bytes=15,
        now=now,
    )

    assert result.deleted_files == 2
    assert newest.exists()
    assert not middle.exists()
    assert not oldest.exists()
