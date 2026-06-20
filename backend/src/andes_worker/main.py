from __future__ import annotations

import csv
import json
import logging
import math
import os
import signal
import time
from collections.abc import Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from andes_api.storage import JobStore
from andes_core.config import get_settings
from andes_core.engine import AndesEngine
from andes_core.schemas import AnalysisKind, GseaRequest, JobRecord, SetSimilarityRequest

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("andes_worker")


def _mapping_report_rows(result: dict[str, Any]) -> list[list[object]]:
    rows: list[list[object]] = [
        [
            "collection",
            "submitted_id",
            "mapped_id",
            "detected_type",
            "source",
            "status",
            "candidates",
        ]
    ]
    parameters = result.get("parameters")
    if not isinstance(parameters, dict):
        return rows
    id_mapping = parameters.get("id_mapping")
    if not isinstance(id_mapping, dict):
        return rows
    for collection, payload in sorted(id_mapping.items()):
        if not isinstance(payload, dict):
            continue
        records = payload.get("records")
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            mapped = record.get("mapped")
            source = record.get("source", "")
            candidates = record.get("candidates", [])
            if not isinstance(candidates, list | tuple):
                candidates = []
            rows.append(
                [
                    collection,
                    record.get("submitted", ""),
                    mapped or "",
                    record.get("id_type", ""),
                    source,
                    "mapped" if mapped else source if source == "ambiguous" else "unmapped",
                    "|".join(str(candidate) for candidate in candidates),
                ]
            )
    return rows


def _write_csv(path, rows: list[list[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerows(_csv_safe_row(row) for row in rows)


def _csv_safe(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return value
    text = str(value)
    check = text.lstrip(" \t\r\n")
    if check.startswith(("=", "+", "-", "@")):
        return f"'{text}"
    return text


def _csv_safe_row(row: Iterable[object]) -> list[object]:
    return [_csv_safe(value) for value in row]


def _write_mapping_report_artifact(result: dict[str, Any], artifact_dir) -> None:
    rows = _mapping_report_rows(result)
    if len(rows) <= 1:
        return
    _write_csv(artifact_dir / "mapping-report.csv", rows)


def _strip_mapping_records(result: dict[str, Any]) -> dict[str, Any]:
    parameters = result.get("parameters")
    if not isinstance(parameters, dict):
        return result
    id_mapping = parameters.get("id_mapping")
    if not isinstance(id_mapping, dict):
        return result
    for payload in id_mapping.values():
        if not isinstance(payload, dict):
            continue
        if payload.pop("records", None) is not None:
            payload["mapping_report"] = "mapping-report.csv"
    return result


def log_event(event: str, *, level: int = logging.INFO, **fields: Any) -> None:
    logger.log(level, json.dumps({"event": event, **fields}, sort_keys=True))


def effective_parallelism(job_concurrency: int, workers_per_job: int) -> int:
    return max(1, job_concurrency) * max(1, workers_per_job)


class Worker:
    def __init__(self):
        self.settings = get_settings()
        self.store = JobStore(
            self.settings.sqlite_path,
            self.settings.runs_dir,
            token_hash_secret=self.settings.token_hash_secret,
        )
        self.job_concurrency = max(1, self.settings.job_concurrency)
        self._warn_if_oversubscribed()
        self.executor = ThreadPoolExecutor(
            max_workers=self.job_concurrency,
            thread_name_prefix="andes-worker",
        )
        self.futures: set[Future[bool]] = set()
        self.running = True

    def stop(self, *_args) -> None:
        self.running = False

    def _warn_if_oversubscribed(self) -> None:
        cpu_count = os.cpu_count()
        if cpu_count is None or cpu_count < 1:
            return
        workers_per_job = max(1, self.settings.workers)
        effective_slots = effective_parallelism(self.job_concurrency, workers_per_job)
        if effective_slots <= cpu_count:
            return
        log_event(
            "worker_parallelism_exceeds_cpu",
            level=logging.WARNING,
            job_concurrency=self.job_concurrency,
            workers_per_job=workers_per_job,
            effective_parallelism=effective_slots,
            cpu_count=cpu_count,
        )

    def _recover_stale_jobs(self) -> None:
        recovered = self.store.recover_stale_running(
            timeout_seconds=self.settings.running_job_timeout_seconds
        )
        if recovered.recovered_jobs:
            log_event(
                "stale_jobs_recovered",
                recovered_jobs=recovered.recovered_jobs,
                recovered_ids=recovered.recovered_ids,
            )

    def _run_job(self, job: JobRecord) -> bool:
        started = time.perf_counter()
        log_event("job_started", job_id=job.id, kind=job.kind.value)
        try:
            payload = self.store.read_input(job.id)
            artifact_dir = self.store.run_dir(job.id) / "downloads"
            engine = AndesEngine(self.settings)
            if job.kind == AnalysisKind.SET_SIMILARITY:
                result = engine.run_set_similarity(
                    SetSimilarityRequest.model_validate(payload),
                    artifact_dir=artifact_dir,
                )
            elif job.kind == AnalysisKind.GSEA:
                result = engine.run_gsea(
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
            result_payload = result.model_dump(mode="json")
            _write_mapping_report_artifact(result_payload, artifact_dir)
            self.store.write_result(job.id, _strip_mapping_records(result_payload))
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

    def _drain_finished(self) -> int:
        completed = 0
        for future in list(self.futures):
            if not future.done():
                continue
            self.futures.remove(future)
            completed += 1
            try:
                future.result()
            except Exception as exc:  # pragma: no cover - _run_job catches job failures.
                log_event("worker_task_failed", error=f"{type(exc).__name__}: {exc}")
        return completed

    def _claim_available_jobs(self) -> int:
        claimed = 0
        while self.running and len(self.futures) < self.job_concurrency:
            job = self.store.claim_next()
            if job is None:
                break
            self.futures.add(self.executor.submit(self._run_job, job))
            claimed += 1
        return claimed

    def run_once(self) -> bool:
        completed = self._drain_finished()
        if not self.futures:
            self._recover_stale_jobs()
        claimed = self._claim_available_jobs()
        return completed > 0 or claimed > 0

    def close(self, *, wait: bool = True) -> None:
        self.executor.shutdown(wait=wait)

    def run_forever(self, poll_seconds: float = 1.0) -> None:
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)
        try:
            while self.running:
                did_work = self.run_once()
                if not did_work:
                    time.sleep(poll_seconds)
        finally:
            self.close()


def main() -> None:
    Worker().run_forever()


if __name__ == "__main__":
    main()
