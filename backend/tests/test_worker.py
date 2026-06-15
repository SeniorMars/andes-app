from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any

from andes_api.storage import StaleRecoveryResult
from andes_core.schemas import AnalysisKind, JobRecord, JobState
from andes_worker.main import Worker, effective_parallelism


class FakeStore:
    def __init__(self, jobs: list[JobRecord]):
        self.jobs = jobs
        self.claimed_ids: list[str] = []
        self.recovery_calls = 0

    def recover_stale_running(self, *, timeout_seconds: int) -> StaleRecoveryResult:
        self.recovery_calls += 1
        return StaleRecoveryResult(recovered_jobs=0, recovered_ids=[])

    def claim_next(self) -> JobRecord | None:
        if not self.jobs:
            return None
        job = self.jobs.pop(0)
        self.claimed_ids.append(job.id)
        return job


def _job(job_id: str) -> JobRecord:
    return JobRecord(
        id=job_id,
        kind=AnalysisKind.SET_SIMILARITY,
        state=JobState.QUEUED,
        created_at="2026-01-01T00:00:00+00:00",
    )


def test_worker_claims_jobs_up_to_configured_concurrency():
    release = Event()
    store = FakeStore([_job("a"), _job("b"), _job("c")])
    worker = Worker.__new__(Worker)
    worker.settings = SimpleNamespace(running_job_timeout_seconds=3600)
    worker.store = store
    worker.job_concurrency = 2
    worker.executor = ThreadPoolExecutor(max_workers=2)
    worker.futures = set()
    worker.running = True

    def run_job(_job: JobRecord) -> bool:
        release.wait(timeout=2)
        return True

    worker._run_job = run_job

    try:
        assert worker.run_once() is True
        assert store.claimed_ids == ["a", "b"]
        assert len(worker.futures) == 2

        release.set()
        for future in list(worker.futures):
            assert future.result(timeout=2) is True

        assert worker.run_once() is True
        assert store.claimed_ids == ["a", "b", "c"]
    finally:
        release.set()
        worker.close()


def test_worker_skips_stale_recovery_while_local_jobs_are_running():
    release = Event()
    store = FakeStore([_job("a")])
    worker = Worker.__new__(Worker)
    worker.settings = SimpleNamespace(running_job_timeout_seconds=3600)
    worker.store = store
    worker.job_concurrency = 1
    worker.executor = ThreadPoolExecutor(max_workers=1)
    worker.futures = set()
    worker.running = True

    def run_job(_job: JobRecord) -> bool:
        release.wait(timeout=2)
        return True

    worker._run_job = run_job

    try:
        assert worker.run_once() is True
        assert store.recovery_calls == 1
        assert len(worker.futures) == 1

        assert worker.run_once() is False
        assert store.recovery_calls == 1

        release.set()
        for future in list(worker.futures):
            assert future.result(timeout=2) is True

        assert worker.run_once() is True
        assert store.recovery_calls == 2
    finally:
        release.set()
        worker.close()


def test_worker_constructs_engine_per_job(monkeypatch, tmp_path):
    constructed_engines: list[object] = []
    used_engines: list[object] = []

    class FakeResult:
        parameters: dict[str, object] = {}

        def model_dump(self, *, mode: str) -> dict[str, object]:
            return {"kind": AnalysisKind.SET_SIMILARITY.value, "results": []}

    class FakeEngine:
        def __init__(self, settings: object):
            self.settings = settings
            constructed_engines.append(self)

        def run_set_similarity(self, request: object, *, artifact_dir: Path) -> FakeResult:
            used_engines.append(self)
            return FakeResult()

    class RunStore:
        def __init__(self):
            self.written_results: list[dict[str, object]] = []
            self.succeeded_ids: list[str] = []

        def read_input(self, job_id: str) -> dict[str, Any]:
            return {"genes": ["A"]}

        def run_dir(self, job_id: str) -> Path:
            return tmp_path / job_id

        def is_cancelled(self, job_id: str) -> bool:
            return False

        def write_result(self, job_id: str, result: dict[str, object]) -> None:
            self.written_results.append(result)

        def mark_succeeded(self, job_id: str) -> bool:
            self.succeeded_ids.append(job_id)
            return True

        def mark_failed(self, job_id: str, error: str) -> bool:
            raise AssertionError(error)

    monkeypatch.setattr("andes_worker.main.AndesEngine", FakeEngine)

    store = RunStore()
    worker = Worker.__new__(Worker)
    worker.settings = SimpleNamespace()
    worker.store = store

    assert worker._run_job(_job("a")) is True
    assert worker._run_job(_job("b")) is True

    assert len(constructed_engines) == 2
    assert used_engines == constructed_engines
    assert len(set(map(id, used_engines))) == 2
    assert store.succeeded_ids == ["a", "b"]
    assert len(store.written_results) == 2


def test_effective_parallelism_multiplies_jobs_by_workers():
    assert effective_parallelism(job_concurrency=4, workers_per_job=8) == 32
    assert effective_parallelism(job_concurrency=0, workers_per_job=0) == 1


def test_worker_warns_when_effective_parallelism_exceeds_cpu(monkeypatch, caplog):
    monkeypatch.setattr("andes_worker.main.os.cpu_count", lambda: 8)

    worker = Worker.__new__(Worker)
    worker.settings = SimpleNamespace(workers=8)
    worker.job_concurrency = 2

    with caplog.at_level(logging.WARNING, logger="andes_worker"):
        worker._warn_if_oversubscribed()

    assert len(caplog.records) == 1
    payload = json.loads(caplog.records[0].message)
    assert payload == {
        "event": "worker_parallelism_exceeds_cpu",
        "job_concurrency": 2,
        "workers_per_job": 8,
        "effective_parallelism": 16,
        "cpu_count": 8,
    }
