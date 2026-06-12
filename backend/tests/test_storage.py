from __future__ import annotations

import os

from andes_api.storage import JobStore
from andes_core.schemas import AnalysisKind, JobState


def test_job_store_claim_and_finish(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3", tmp_path / "runs")
    job = store.create_job(AnalysisKind.SET_SIMILARITY, {"genes": ["A"]})

    queued = store.get_job(job.id)
    assert queued is not None
    assert queued.state == JobState.QUEUED

    claimed = store.claim_next()
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.state == JobState.RUNNING
    assert store.claim_next() is None

    store.write_result(job.id, {"ok": True})
    store.mark_succeeded(job.id)
    done = store.get_job(job.id)
    assert done is not None
    assert done.state == JobState.SUCCEEDED
    assert store.read_result(job.id) == {"ok": True}


def test_queue_position_and_cancel(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3", tmp_path / "runs")
    first = store.create_job(AnalysisKind.SET_SIMILARITY, {"genes": ["A"]}, owner_key="ip:one")
    second = store.create_job(AnalysisKind.GSEA, {"ranked_genes": [["A", 1.0]]}, owner_key="ip:one")

    assert store.queued_count() == 2
    assert store.active_count_for_owner("ip:one") == 2
    assert store.queue_status(first.id)["position"] == 1
    assert store.queue_status(second.id)["position"] == 2

    cancelled = store.cancel_job(second.id)

    assert cancelled is not None
    assert cancelled.cancelled is True
    assert cancelled.job.state == JobState.CANCELLED
    assert store.queued_count() == 1
    assert store.queue_status(second.id)["position"] is None


def test_cancel_running_prevents_success_transition(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3", tmp_path / "runs")
    job = store.create_job(AnalysisKind.SET_SIMILARITY, {"genes": ["A"]})
    claimed = store.claim_next()
    assert claimed is not None

    cancelled = store.cancel_job(job.id)
    marked = store.mark_succeeded(job.id)

    assert cancelled is not None
    assert cancelled.cancelled is True
    assert marked is False
    assert store.get_job(job.id).state == JobState.CANCELLED  # type: ignore[union-attr]


def test_recover_stale_running_jobs(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3", tmp_path / "runs")
    old = store.create_job(AnalysisKind.SET_SIMILARITY, {"genes": ["A"]})
    fresh = store.create_job(AnalysisKind.GSEA, {"ranked_genes": [["A", 1.0]]})
    assert store.claim_next().id == old.id  # type: ignore[union-attr]
    assert store.claim_next().id == fresh.id  # type: ignore[union-attr]

    with store.connect() as conn:
        conn.execute(
            "UPDATE jobs SET started_at = ? WHERE id = ?",
            ("1970-01-01T00:00:00+00:00", old.id),
        )
        conn.execute(
            "UPDATE jobs SET started_at = ? WHERE id = ?",
            ("1970-01-02T00:00:00+00:00", fresh.id),
        )

    result = store.recover_stale_running(timeout_seconds=3600, now=90_000.0)

    assert result.recovered_jobs == 1
    assert result.recovered_ids == [old.id]
    assert store.get_job(old.id).state == JobState.FAILED  # type: ignore[union-attr]
    assert store.get_job(fresh.id).state == JobState.RUNNING  # type: ignore[union-attr]


def test_prune_finished_jobs_deletes_old_runs(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3", tmp_path / "runs")
    old_job = store.create_job(AnalysisKind.SET_SIMILARITY, {"genes": ["A"]})
    recent_job = store.create_job(AnalysisKind.GSEA, {"ranked_genes": [["A", 1.0]]})
    (store.run_dir(old_job.id) / "payload.txt").write_text("old", encoding="utf-8")
    (store.run_dir(recent_job.id) / "payload.txt").write_text("recent", encoding="utf-8")
    assert store.claim_next().id == old_job.id  # type: ignore[union-attr]
    store.mark_succeeded(old_job.id)
    assert store.claim_next().id == recent_job.id  # type: ignore[union-attr]
    store.mark_succeeded(recent_job.id)
    now = 4_000_000.0
    old_time = now - 40 * 86400
    os.utime(store.run_dir(old_job.id), (old_time, old_time))

    with store.connect() as conn:
        conn.execute(
            "UPDATE jobs SET finished_at = ? WHERE id = ?",
            ("1970-01-07T13:46:40+00:00", old_job.id),
        )
        conn.execute(
            "UPDATE jobs SET finished_at = ? WHERE id = ?",
            ("1970-01-12T10:30:00+00:00", recent_job.id),
        )

    result = store.prune_finished_jobs(max_age_days=30, min_keep_jobs=1, now=now)

    assert result.scanned_jobs == 2
    assert result.deleted_jobs == 1
    assert store.get_job(old_job.id) is None
    assert not store.run_dir(old_job.id).exists()
    assert store.get_job(recent_job.id) is not None
