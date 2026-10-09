"""
Background ingest jobs with progress reporting and cancellation.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class IngestCancelled(Exception):
    """Raised when a job is cancelled mid-flight."""


@dataclass
class IngestJob:
    id: str
    label: str
    kind: str  # "file" | "url"
    status: JobStatus = JobStatus.QUEUED
    progress: float = 0.0
    stage: str = "queued"
    message: str = "Queued"
    source: str = ""
    chunks_added: int = 0
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "status": self.status.value,
            "progress": round(self.progress, 1),
            "stage": self.stage,
            "message": self.message,
            "source": self.source,
            "chunks_added": self.chunks_added,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "cancellable": self.status in (JobStatus.QUEUED, JobStatus.RUNNING),
        }


_jobs: dict[str, IngestJob] = {}
_lock = threading.Lock()
_MAX_JOBS = 100


def _touch(job: IngestJob) -> None:
    job.updated_at = time.time()


def update_job(
    job: IngestJob,
    *,
    status: Optional[JobStatus] = None,
    progress: Optional[float] = None,
    stage: Optional[str] = None,
    message: Optional[str] = None,
    source: Optional[str] = None,
    chunks_added: Optional[int] = None,
    error: Optional[str] = None,
) -> None:
    with _lock:
        if status is not None:
            job.status = status
        if progress is not None:
            job.progress = max(0.0, min(100.0, progress))
        if stage is not None:
            job.stage = stage
        if message is not None:
            job.message = message
        if source is not None:
            job.source = source
        if chunks_added is not None:
            job.chunks_added = chunks_added
        if error is not None:
            job.error = error
        _touch(job)


def check_cancelled(job: IngestJob) -> None:
    if job._cancel.is_set():
        raise IngestCancelled("Ingest cancelled by user")


def create_job(label: str, kind: str) -> IngestJob:
    job = IngestJob(id=str(uuid.uuid4()), label=label, kind=kind)
    with _lock:
        # prune oldest finished jobs
        if len(_jobs) >= _MAX_JOBS:
            finished = [
                j for j in _jobs.values()
                if j.status in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED)
            ]
            finished.sort(key=lambda j: j.updated_at)
            for old in finished[: max(1, len(finished) // 2)]:
                _jobs.pop(old.id, None)
        _jobs[job.id] = job
    return job


def get_job(job_id: str) -> Optional[IngestJob]:
    with _lock:
        return _jobs.get(job_id)


def cancel_job(job_id: str) -> IngestJob:
    job = get_job(job_id)
    if job is None:
        raise KeyError(job_id)
    if job.status in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED):
        return job
    job._cancel.set()
    if job.status == JobStatus.QUEUED:
        update_job(
            job,
            status=JobStatus.CANCELLED,
            progress=0,
            stage="cancelled",
            message="Cancelled before start",
        )
    else:
        update_job(job, stage="cancelling", message="Cancelling…")
    return job


def run_file_job(job: IngestJob, file_path: str) -> None:
    from backend.ingestion import ingest_file, get_pdf_ingest_message
    from backend.vector_store import add_documents

    try:
        check_cancelled(job)
        update_job(
            job,
            status=JobStatus.RUNNING,
            progress=5,
            stage="parsing",
            message=f"Parsing {Path(file_path).name}…",
            source=file_path,
        )

        def on_progress(done: int, total: int, detail: str) -> None:
            check_cancelled(job)
            # parsing occupies 5–70%
            pct = 5 + (65 * done / max(total, 1))
            update_job(job, progress=pct, stage="parsing", message=detail)

        docs = ingest_file(
            file_path,
            cancel_check=lambda: check_cancelled(job),
            progress_cb=on_progress,
        )
        check_cancelled(job)

        update_job(
            job,
            progress=75,
            stage="indexing",
            message=f"Embedding & indexing {len(docs)} chunks…",
        )
        count = add_documents(docs)
        check_cancelled(job)

        suffix = Path(file_path).suffix.lower()
        if suffix == ".pdf":
            message = get_pdf_ingest_message(file_path, count)
        elif count == 0:
            message = (
                f"Ingested {Path(file_path).name} but added 0 chunks — "
                "file may be empty or unreadable."
            )
        else:
            message = f"Successfully ingested {Path(file_path).name} into {count} chunks."

        update_job(
            job,
            status=JobStatus.SUCCEEDED,
            progress=100,
            stage="done",
            message=message,
            chunks_added=count,
        )
    except IngestCancelled:
        update_job(
            job,
            status=JobStatus.CANCELLED,
            stage="cancelled",
            message="Cancelled",
            progress=job.progress,
        )
        logger.info("Ingest job %s cancelled (%s)", job.id, job.label)
    except Exception as e:
        logger.exception("Ingest job %s failed (%s)", job.id, job.label)
        update_job(
            job,
            status=JobStatus.FAILED,
            stage="error",
            message="Ingest failed",
            error=str(e),
        )


def run_url_job(job: IngestJob, url: str) -> None:
    from backend.ingestion import ingest_source
    from backend.vector_store import add_documents

    try:
        check_cancelled(job)
        update_job(
            job,
            status=JobStatus.RUNNING,
            progress=10,
            stage="fetching",
            message=f"Fetching {url}…",
            source=url,
        )
        check_cancelled(job)

        def on_progress(done: int, total: int, detail: str) -> None:
            check_cancelled(job)
            pct = 10 + (55 * done / max(total, 1))
            update_job(job, progress=pct, stage="parsing", message=detail)

        docs = ingest_source(
            url,
            cancel_check=lambda: check_cancelled(job),
            progress_cb=on_progress,
        )
        check_cancelled(job)

        update_job(
            job,
            progress=80,
            stage="indexing",
            message=f"Embedding & indexing {len(docs)} chunks…",
        )
        count = add_documents(docs)
        check_cancelled(job)

        msg = f"Successfully ingested URL into {count} chunks."
        if count == 0:
            msg = "Ingested URL but added 0 chunks — page may have no readable text."

        update_job(
            job,
            status=JobStatus.SUCCEEDED,
            progress=100,
            stage="done",
            message=msg,
            chunks_added=count,
        )
    except IngestCancelled:
        update_job(
            job,
            status=JobStatus.CANCELLED,
            stage="cancelled",
            message="Cancelled",
            progress=job.progress,
        )
        logger.info("URL ingest job %s cancelled (%s)", job.id, url)
    except Exception as e:
        logger.exception("URL ingest job %s failed (%s)", job.id, url)
        update_job(
            job,
            status=JobStatus.FAILED,
            stage="error",
            message="Ingest failed",
            error=str(e),
        )


def start_thread(target: Callable[..., None], *args: Any) -> None:
    t = threading.Thread(target=target, args=args, daemon=True)
    t.start()


def validate_url(url: str) -> str:
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("URL must start with http:// or https://")
    return url


def safe_filename(name: Optional[str]) -> str:
    if not name or not name.strip():
        raise ValueError("Missing filename")
    cleaned = Path(name).name.strip()
    if not cleaned or cleaned in (".", ".."):
        raise ValueError("Invalid filename")
    return cleaned
