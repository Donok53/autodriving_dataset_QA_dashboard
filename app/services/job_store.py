from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Lock
from uuid import uuid4


@dataclass
class AnalysisJob:
    job_id: str
    filename: str
    source_type: str
    status: str = "pending"
    progress: int = 0
    stage: str = "대기 중"
    error: str | None = None
    result: dict[str, object] | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self, include_result: bool = False) -> dict[str, object]:
        payload: dict[str, object] = {
            "job_id": self.job_id,
            "filename": self.filename,
            "source_type": self.source_type,
            "status": self.status,
            "progress": self.progress,
            "stage": self.stage,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
        if include_result:
            payload["result"] = self.result
        return payload


_jobs: dict[str, AnalysisJob] = {}
_jobs_lock = Lock()
_job_ttl = timedelta(hours=2)


def create_job(filename: str, source_type: str) -> AnalysisJob:
    with _jobs_lock:
        _prune_expired_jobs()
        job = AnalysisJob(
            job_id=uuid4().hex,
            filename=filename,
            source_type=source_type,
            status="pending",
            progress=0,
            stage="업로드 준비 중",
        )
        _jobs[job.job_id] = job
        return job


def get_job(job_id: str) -> AnalysisJob | None:
    with _jobs_lock:
        return _jobs.get(job_id)


def update_job(
    job_id: str,
    *,
    status: str | None = None,
    progress: int | None = None,
    stage: str | None = None,
    error: str | None = None,
    result: dict[str, object] | None = None,
) -> AnalysisJob | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return None

        if status is not None:
            job.status = status
        if progress is not None:
            job.progress = max(0, min(progress, 100))
        if stage is not None:
            job.stage = stage
        if error is not None:
            job.error = error
        if result is not None:
            job.result = result
        job.updated_at = datetime.now(timezone.utc)
        return job


def _prune_expired_jobs() -> None:
    now = datetime.now(timezone.utc)
    expired_job_ids = [
        job_id
        for job_id, job in _jobs.items()
        if now - job.updated_at > _job_ttl
    ]
    for job_id in expired_job_ids:
        del _jobs[job_id]
