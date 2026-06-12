from __future__ import annotations

import json
import logging
import signal
import time
from typing import Any

from andes_api.storage import JobStore
from andes_core.config import get_settings
from andes_core.engine import AndesEngine
from andes_core.schemas import AnalysisKind, GseaRequest, SetSimilarityRequest

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("andes_worker")


def log_event(event: str, **fields: Any) -> None:
    logger.info(json.dumps({"event": event, **fields}, sort_keys=True))


class Worker:
    def __init__(self):
        self.settings = get_settings()
        self.store = JobStore(self.settings.sqlite_path, self.settings.runs_dir)
        self.engine = AndesEngine(self.settings)
        self.running = True

    def stop(self, *_args) -> None:
        self.running = False

    def run_once(self) -> bool:
        recovered = self.store.recover_stale_running(
            timeout_seconds=self.settings.running_job_timeout_seconds
        )
        if recovered.recovered_jobs:
            log_event(
                "stale_jobs_recovered",
                recovered_jobs=recovered.recovered_jobs,
                recovered_ids=recovered.recovered_ids,
            )
        job = self.store.claim_next()
        if job is None:
            return False
        started = time.perf_counter()
        log_event("job_started", job_id=job.id, kind=job.kind.value)
        try:
            payload = self.store.read_input(job.id)
            artifact_dir = self.store.run_dir(job.id) / "downloads"
            if job.kind == AnalysisKind.SET_SIMILARITY:
                result = self.engine.run_set_similarity(
                    SetSimilarityRequest.model_validate(payload),
                    artifact_dir=artifact_dir,
                )
            elif job.kind == AnalysisKind.GSEA:
                result = self.engine.run_gsea(
                    GseaRequest.model_validate(payload),
                    artifact_dir=artifact_dir,
                )
            else:
                raise ValueError(f"unknown job kind: {job.kind}")
            if self.store.is_cancelled(job.id):
                log_event(
                    "job_cancelled_after_run",
                    job_id=job.id,
                    kind=job.kind.value,
                    elapsed_seconds=round(time.perf_counter() - started, 6),
                )
                return True
            self.store.write_result(job.id, result.model_dump(mode="json"))
            marked = self.store.mark_succeeded(job.id)
            timing = result.parameters.get("timing_seconds", {})
            cache = result.parameters.get("cache", {})
            log_event(
                "job_succeeded",
                job_id=job.id,
                kind=job.kind.value,
                marked_succeeded=marked,
                elapsed_seconds=round(time.perf_counter() - started, 6),
                timing_seconds=timing,
                cache=cache,
            )
        except Exception as exc:
            if self.store.is_cancelled(job.id):
                log_event(
                    "job_cancelled_after_error",
                    job_id=job.id,
                    kind=job.kind.value,
                    elapsed_seconds=round(time.perf_counter() - started, 6),
                    error=f"{type(exc).__name__}: {exc}",
                )
                return True
            marked = self.store.mark_failed(job.id, f"{type(exc).__name__}: {exc}")
            log_event(
                "job_failed",
                job_id=job.id,
                kind=job.kind.value,
                marked_failed=marked,
                elapsed_seconds=round(time.perf_counter() - started, 6),
                error=f"{type(exc).__name__}: {exc}",
            )
        return True

    def run_forever(self, poll_seconds: float = 1.0) -> None:
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)
        while self.running:
            did_work = self.run_once()
            if not did_work:
                time.sleep(poll_seconds)


def main() -> None:
    Worker().run_forever()


if __name__ == "__main__":
    main()
