from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from andes_core.schemas import AnalysisKind, JobRecord, JobState


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class JobPruneResult:
    scanned_jobs: int
    deleted_jobs: int
    kept_jobs: int
    deleted_bytes: int
    dry_run: bool


@dataclass(frozen=True)
class CancelJobResult:
    job: JobRecord
    cancelled: bool


@dataclass(frozen=True)
class StaleRecoveryResult:
    recovered_jobs: int
    recovered_ids: list[str]


class JobStore:
    def __init__(self, sqlite_path: Path, runs_dir: Path):
        self.sqlite_path = sqlite_path.expanduser().resolve()
        self.runs_dir = runs_dir.expanduser().resolve()
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.init_db()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.sqlite_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    cancelled_at TEXT,
                    owner_key TEXT,
                    error TEXT
                )
                """
            )
            self._ensure_column(conn, "cancelled_at", "TEXT")
            self._ensure_column(conn, "owner_key", "TEXT")

    def _ensure_column(self, conn: sqlite3.Connection, name: str, column_type: str) -> None:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        if name not in columns:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {column_type}")

    def create_job(
        self,
        kind: AnalysisKind,
        payload: dict[str, Any],
        *,
        files: dict[str, str] | None = None,
        path_fields: dict[str, str] | None = None,
        owner_key: str | None = None,
    ) -> JobRecord:
        job_id = uuid.uuid4().hex
        run_dir = self.run_dir(job_id)
        run_dir.mkdir(parents=True, exist_ok=False)
        payload = dict(payload)
        for relative_path, contents in (files or {}).items():
            path = run_dir / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents, encoding="utf-8")
        for field, relative_path in (path_fields or {}).items():
            payload[field] = str((run_dir / relative_path).resolve())
        (run_dir / "input.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        created_at = now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs (id, kind, state, created_at, owner_key)
                VALUES (?, ?, ?, ?, ?)
                """,
                (job_id, kind.value, JobState.QUEUED.value, created_at, owner_key),
            )
        return JobRecord(
            id=job_id,
            kind=kind,
            state=JobState.QUEUED,
            created_at=created_at,
            owner_key=owner_key,
        )

    def get_job(self, job_id: str) -> JobRecord | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._record(row) if row else None

    def claim_next(self) -> JobRecord | None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM jobs WHERE state = ? ORDER BY created_at LIMIT 1",
                (JobState.QUEUED.value,),
            ).fetchone()
            if not row:
                return None
            started_at = now_iso()
            updated_rows = conn.execute(
                "UPDATE jobs SET state = ?, started_at = ? WHERE id = ? AND state = ?",
                (JobState.RUNNING.value, started_at, row["id"], JobState.QUEUED.value),
            ).rowcount
            if updated_rows != 1:
                return None
            updated = conn.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()
        return self._record(updated)

    def mark_succeeded(self, job_id: str) -> bool:
        with self.connect() as conn:
            return (
                conn.execute(
                    """
                    UPDATE jobs SET state = ?, finished_at = ?, error = NULL
                    WHERE id = ? AND state = ?
                    """,
                    (JobState.SUCCEEDED.value, now_iso(), job_id, JobState.RUNNING.value),
                ).rowcount
                == 1
            )

    def mark_failed(self, job_id: str, error: str) -> bool:
        run_dir = self.run_dir(job_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "error.txt").write_text(error, encoding="utf-8")
        with self.connect() as conn:
            return (
                conn.execute(
                    """
                    UPDATE jobs SET state = ?, finished_at = ?, error = ?
                    WHERE id = ? AND state != ?
                    """,
                    (JobState.FAILED.value, now_iso(), error, job_id, JobState.CANCELLED.value),
                ).rowcount
                == 1
            )

    def cancel_job(
        self,
        job_id: str,
        *,
        reason: str = "cancelled by user",
    ) -> CancelJobResult | None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            if row["state"] not in (JobState.QUEUED.value, JobState.RUNNING.value):
                return CancelJobResult(job=self._record(row), cancelled=False)
            timestamp = now_iso()
            conn.execute(
                """
                UPDATE jobs
                SET state = ?, finished_at = ?, cancelled_at = ?, error = ?
                WHERE id = ?
                """,
                (JobState.CANCELLED.value, timestamp, timestamp, reason, job_id),
            )
            updated = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return CancelJobResult(job=self._record(updated), cancelled=True)

    def is_cancelled(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        return job is not None and job.state == JobState.CANCELLED

    def queued_count(self) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM jobs WHERE state = ?",
                (JobState.QUEUED.value,),
            ).fetchone()
        return int(row["count"])

    def active_count_for_owner(self, owner_key: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count FROM jobs
                WHERE owner_key = ? AND state IN (?, ?)
                """,
                (owner_key, JobState.QUEUED.value, JobState.RUNNING.value),
            ).fetchone()
        return int(row["count"])

    def queue_status(self, job_id: str) -> dict[str, int | str | None]:
        job = self.get_job(job_id)
        if job is None:
            return {"state": None, "position": None, "queued_ahead": 0}
        if job.state == JobState.RUNNING:
            return {"state": job.state.value, "position": 0, "queued_ahead": 0}
        if job.state != JobState.QUEUED:
            return {"state": job.state.value, "position": None, "queued_ahead": 0}
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count FROM jobs
                WHERE state = ?
                  AND (created_at < ? OR (created_at = ? AND id <= ?))
                """,
                (JobState.QUEUED.value, job.created_at, job.created_at, job.id),
            ).fetchone()
        position = int(row["count"])
        return {
            "state": job.state.value,
            "position": position,
            "queued_ahead": max(0, position - 1),
        }

    def list_jobs(self, *, limit: int = 100) -> list[JobRecord]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM jobs
                ORDER BY
                  CASE state
                    WHEN ? THEN 0
                    WHEN ? THEN 1
                    ELSE 2
                  END,
                  created_at DESC
                LIMIT ?
                """,
                (JobState.RUNNING.value, JobState.QUEUED.value, limit),
            ).fetchall()
        return [self._record(row) for row in rows]

    def queue_entries(self, *, limit: int = 100) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for job in self.list_jobs(limit=limit):
            entry = job.model_dump(mode="json")
            entry["queue"] = self.queue_status(job.id)
            entries.append(entry)
        return entries

    def recover_stale_running(
        self,
        *,
        timeout_seconds: int,
        now: float | None = None,
    ) -> StaleRecoveryResult:
        if timeout_seconds <= 0:
            return StaleRecoveryResult(recovered_jobs=0, recovered_ids=[])
        timestamp = datetime.now(UTC).timestamp() if now is None else now
        cutoff = timestamp - timeout_seconds
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE state = ? AND started_at IS NOT NULL",
                (JobState.RUNNING.value,),
            ).fetchall()
            stale_ids = [
                row["id"]
                for row in rows
                if _iso_timestamp(row["started_at"]) < cutoff
            ]
            if stale_ids:
                finished_at = datetime.fromtimestamp(timestamp, UTC).isoformat()
                conn.executemany(
                    """
                    UPDATE jobs
                    SET state = ?, finished_at = ?, error = ?
                    WHERE id = ? AND state = ?
                    """,
                    [
                        (
                            JobState.FAILED.value,
                            finished_at,
                            f"stale running job recovered after {timeout_seconds} seconds",
                            job_id,
                            JobState.RUNNING.value,
                        )
                        for job_id in stale_ids
                    ],
                )
        return StaleRecoveryResult(recovered_jobs=len(stale_ids), recovered_ids=stale_ids)

    def read_input(self, job_id: str) -> dict[str, Any]:
        return json.loads((self.run_dir(job_id) / "input.json").read_text(encoding="utf-8"))

    def write_result(self, job_id: str, payload: dict[str, Any]) -> None:
        run_dir = self.run_dir(job_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def read_result(self, job_id: str) -> dict[str, Any] | None:
        path = self.run_dir(job_id) / "results.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def job_counts(self) -> dict[str, int]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT state, COUNT(*) AS count FROM jobs GROUP BY state"
            ).fetchall()
        counts = {state.value: 0 for state in JobState}
        for row in rows:
            counts[str(row["state"])] = int(row["count"])
        return counts

    def storage_status(self) -> dict[str, Any]:
        run_dirs = [path for path in self.runs_dir.iterdir() if path.is_dir()]
        total_bytes = sum(
            file.stat().st_size for path in run_dirs for file in path.rglob("*") if file.is_file()
        )
        return {
            "job_counts": self.job_counts(),
            "run_directories": len(run_dirs),
            "run_bytes": total_bytes,
        }

    def prune_finished_jobs(
        self,
        *,
        max_age_days: int,
        min_keep_jobs: int,
        dry_run: bool = False,
        now: float | None = None,
    ) -> JobPruneResult:
        timestamp = datetime.now(UTC).timestamp() if now is None else now
        cutoff = timestamp - (max_age_days * 86400)
        terminal_states = (
            JobState.SUCCEEDED.value,
            JobState.FAILED.value,
            JobState.CANCELLED.value,
        )
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM jobs
                WHERE state IN (?, ?, ?) AND finished_at IS NOT NULL
                ORDER BY finished_at DESC
                """,
                terminal_states,
            ).fetchall()

        protected_ids = {row["id"] for row in rows[: max(0, min_keep_jobs)]}
        delete_ids: list[str] = []
        for row in rows:
            if row["id"] in protected_ids:
                continue
            if _iso_timestamp(row["finished_at"]) < cutoff:
                delete_ids.append(row["id"])

        deleted_bytes = sum(_directory_size(self.run_dir(job_id)) for job_id in delete_ids)
        if not dry_run and delete_ids:
            for job_id in delete_ids:
                shutil.rmtree(self.run_dir(job_id), ignore_errors=True)
            with self.connect() as conn:
                conn.executemany(
                    "DELETE FROM jobs WHERE id = ?", [(job_id,) for job_id in delete_ids]
                )

        return JobPruneResult(
            scanned_jobs=len(rows),
            deleted_jobs=len(delete_ids),
            kept_jobs=len(rows) - len(delete_ids),
            deleted_bytes=deleted_bytes,
            dry_run=dry_run,
        )

    def run_dir(self, job_id: str) -> Path:
        return self.runs_dir / job_id

    def _record(self, row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            id=row["id"],
            kind=AnalysisKind(row["kind"]),
            state=JobState(row["state"]),
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            cancelled_at=row["cancelled_at"],
            error=row["error"],
            owner_key=row["owner_key"],
        )


def _iso_timestamp(value: str) -> float:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(file.stat().st_size for file in path.rglob("*") if file.is_file())
