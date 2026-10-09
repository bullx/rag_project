"""
FastAPI Backend
Endpoints:
  POST /ingest/file          — sync upload & ingest (CLI / simple clients)
  POST /ingest/url           — sync URL ingest
  POST /ingest/file/jobs     — async file ingest (progress + cancel)
  POST /ingest/url/jobs      — async URL ingest (progress + cancel)
  GET  /ingest/jobs/{id}     — job status / progress
  POST /ingest/jobs/{id}/cancel
  POST /ask                  — single Q&A with optional chat history
  POST /research             — streaming research summary on a topic
  GET  /sources              — list all ingested sources
  DELETE /sources            — delete a source from the knowledge base
  GET  /stats                — knowledge base statistics
  GET  /health               — health check
"""

import os
import shutil
import logging
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from starlette.concurrency import iterate_in_threadpool

load_dotenv()

from backend.logging_config import setup_logging, check_tesseract

log_file = setup_logging()
logger = logging.getLogger(__name__)

if not check_tesseract() and os.getenv("PDF_OCR_ENABLED", "true").lower() == "true":
    logger.warning(
        "Tesseract not found — scanned PDF OCR will fail. Install: brew install tesseract"
    )

from backend.ingestion import ingest_source, ingest_file, get_pdf_ingest_message
from backend.vector_store import (
    add_documents,
    list_sources,
    delete_source,
    collection_stats,
)
from backend.rag_chain import ask, research_summary, retrieve
from backend import ingest_jobs

# ── App setup ─────────────────────────────────────────────────────────────────
app = FastAPI(
    title="RAG API",
    description="Retrieval-augmented generation — ingest docs, ask questions, get research summaries.",
    version="1.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "./data/uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_SUFFIXES = {".pdf", ".docx", ".doc", ".csv", ".json"}


# ── Request / Response models ─────────────────────────────────────────────────

class UrlIngestRequest(BaseModel):
    url: str = Field(..., min_length=8)

class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str

class AskRequest(BaseModel):
    question: str = Field(..., min_length=1)
    chat_history: Optional[List[ChatMessage]] = []
    top_k: Optional[int] = Field(default=6, ge=1, le=100)
    response_mode: Optional[str] = "answer"  # "answer" or "list"

class RetrieveRequest(BaseModel):
    question: str = Field(..., min_length=1)
    top_k: Optional[int] = Field(default=6, ge=1, le=100)

class ResearchRequest(BaseModel):
    topic: str = Field(..., min_length=1)
    top_k: Optional[int] = Field(default=10, ge=1, le=100)

class IngestResponse(BaseModel):
    success: bool
    source: str
    chunks_added: int
    message: str

class AskResponse(BaseModel):
    answer: str
    question: str

class JobResponse(BaseModel):
    id: str
    label: str
    kind: str
    status: str
    progress: float
    stage: str
    message: str
    source: str = ""
    chunks_added: int = 0
    error: Optional[str] = None
    created_at: float
    updated_at: float
    cancellable: bool = False


def _http_error(status: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail=detail)


def _save_upload(file: UploadFile) -> tuple[Path, str]:
    try:
        filename = ingest_jobs.safe_filename(file.filename)
    except ValueError as e:
        raise _http_error(400, str(e)) from e

    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise _http_error(
            400,
            f"Unsupported file type '{suffix}'. Allowed: {sorted(ALLOWED_SUFFIXES)}",
        )

    dest = UPLOAD_DIR / filename
    try:
        with dest.open("wb") as f:
            shutil.copyfileobj(file.file, f)
    except OSError as e:
        logger.exception("Failed to save upload %s", filename)
        raise _http_error(500, f"Failed to save upload: {e}") from e

    if dest.stat().st_size == 0:
        dest.unlink(missing_ok=True)
        raise _http_error(400, f"Uploaded file is empty: {filename}")

    return dest, suffix


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
def root():
    """Redirect root to API docs (React UI runs on the Vite port)."""
    return RedirectResponse(url="/docs")


@app.get("/health")
def health():
    from backend.vector_store import resolve_embedding_device

    cuda_available = False
    torch_version = None
    try:
        import torch
        cuda_available = torch.cuda.is_available()
        torch_version = getattr(torch, "__version__", None)
    except ImportError:
        pass

    return {
        "status": "ok",
        "service": "RAG API",
        "tesseract": check_tesseract(),
        "log_file": str(log_file),
        "embedding_device": resolve_embedding_device(),
        "cuda_available": cuda_available,
        "torch_version": torch_version,
    }


@app.get("/stats")
def stats():
    """Return knowledge base statistics."""
    try:
        return collection_stats()
    except Exception as e:
        logger.exception("stats failed")
        raise _http_error(500, f"Failed to read stats: {e}") from e


@app.get("/sources")
def get_sources():
    """List all ingested sources."""
    try:
        return {"sources": list_sources()}
    except Exception as e:
        logger.exception("list sources failed")
        raise _http_error(500, f"Failed to list sources: {e}") from e


@app.delete("/sources")
def remove_source(source: str):
    """Delete all chunks from a source by its source path/URL."""
    if not source or not source.strip():
        raise _http_error(400, "Query param 'source' is required")
    try:
        deleted = delete_source(source)
    except Exception as e:
        logger.exception("delete source failed: %s", source)
        raise _http_error(500, f"Failed to delete source: {e}") from e
    if deleted == 0:
        raise _http_error(404, f"Source not found: {source}")
    return {"deleted_chunks": deleted, "source": source}


@app.post("/ingest/file", response_model=IngestResponse)
async def ingest_file_endpoint(file: UploadFile = File(...)):
    """Upload and ingest a file into the knowledge base (synchronous)."""
    dest, suffix = _save_upload(file)

    try:
        docs = ingest_file(str(dest))
        count = add_documents(docs)
        if suffix == ".pdf":
            message = get_pdf_ingest_message(str(dest), count)
        elif count == 0:
            message = (
                f"Ingested {dest.name} but added 0 chunks — "
                "file may be empty or unreadable."
            )
        else:
            message = f"Successfully ingested {dest.name} into {count} chunks."
        return IngestResponse(
            success=True,
            source=str(dest),
            chunks_added=count,
            message=message,
        )
    except (ValueError, FileNotFoundError) as e:
        raise _http_error(400, str(e)) from e
    except Exception as e:
        logger.exception("Sync file ingest failed: %s", dest)
        raise _http_error(500, f"Ingest failed: {e}") from e


@app.post("/ingest/url", response_model=IngestResponse)
def ingest_url_endpoint(req: UrlIngestRequest):
    """Fetch and ingest a web URL into the knowledge base (synchronous)."""
    try:
        url = ingest_jobs.validate_url(req.url)
    except ValueError as e:
        raise _http_error(400, str(e)) from e

    try:
        docs = ingest_source(url)
        count = add_documents(docs)
        msg = f"Successfully ingested URL into {count} chunks."
        if count == 0:
            msg = "Ingested URL but added 0 chunks — page may have no readable text."
        return IngestResponse(
            success=True,
            source=url,
            chunks_added=count,
            message=msg,
        )
    except (ValueError, FileNotFoundError, TimeoutError, ConnectionError) as e:
        raise _http_error(400, str(e)) from e
    except Exception as e:
        logger.exception("Sync URL ingest failed: %s", req.url)
        raise _http_error(500, f"Ingest failed: {e}") from e


@app.post("/ingest/file/jobs", response_model=JobResponse)
async def ingest_file_job_endpoint(file: UploadFile = File(...)):
    """Start an async file ingest job (poll GET /ingest/jobs/{id})."""
    dest, _suffix = _save_upload(file)
    job = ingest_jobs.create_job(label=dest.name, kind="file")
    ingest_jobs.update_job(
        job,
        status=ingest_jobs.JobStatus.QUEUED,
        progress=2,
        stage="saved",
        message=f"Saved {dest.name}, starting…",
        source=str(dest),
    )
    ingest_jobs.start_thread(ingest_jobs.run_file_job, job, str(dest))
    return JobResponse(**job.to_dict())


@app.post("/ingest/url/jobs", response_model=JobResponse)
def ingest_url_job_endpoint(req: UrlIngestRequest):
    """Start an async URL ingest job (poll GET /ingest/jobs/{id})."""
    try:
        url = ingest_jobs.validate_url(req.url)
    except ValueError as e:
        raise _http_error(400, str(e)) from e

    job = ingest_jobs.create_job(label=url, kind="url")
    ingest_jobs.update_job(
        job,
        status=ingest_jobs.JobStatus.QUEUED,
        progress=2,
        stage="queued",
        message="Queued",
        source=url,
    )
    ingest_jobs.start_thread(ingest_jobs.run_url_job, job, url)
    return JobResponse(**job.to_dict())


@app.get("/ingest/jobs/{job_id}", response_model=JobResponse)
def get_ingest_job(job_id: str):
    job = ingest_jobs.get_job(job_id)
    if job is None:
        raise _http_error(404, f"Job not found: {job_id}")
    return JobResponse(**job.to_dict())


@app.post("/ingest/jobs/{job_id}/cancel", response_model=JobResponse)
def cancel_ingest_job(job_id: str):
    try:
        job = ingest_jobs.cancel_job(job_id)
    except KeyError:
        raise _http_error(404, f"Job not found: {job_id}") from None
    return JobResponse(**job.to_dict())


@app.post("/retrieve")
def retrieve_endpoint(req: RetrieveRequest):
    """Retrieve relevant chunks without generating an answer."""
    try:
        return retrieve(req.question, top_k=req.top_k)
    except Exception as e:
        logger.exception("retrieve failed")
        raise _http_error(500, f"Retrieve failed: {e}") from e


@app.post("/ask", response_model=AskResponse)
def ask_endpoint(req: AskRequest):
    """Answer a question using the knowledge base (non-streaming)."""
    history = [{"role": m.role, "content": m.content} for m in (req.chat_history or [])]
    try:
        answer = ask(
            req.question,
            chat_history=history,
            top_k=req.top_k,
            stream=False,
            response_mode=req.response_mode or "answer",
        )
        return AskResponse(answer=answer, question=req.question)
    except Exception as e:
        logger.exception("ask failed")
        raise _http_error(500, f"Ask failed: {e}") from e


async def _cancellable_stream(sync_gen, request: Request):
    """
    Bridge a sync Ollama generator to an async stream.
    Stops iteration and force-closes Ollama when the client disconnects / presses Stop.
    """
    iterator = iter(sync_gen)
    try:
        async for chunk in iterate_in_threadpool(iterator):
            if await request.is_disconnected():
                logger.info("Client disconnected — stopping stream")
                break
            yield chunk
    finally:
        force_close = getattr(sync_gen, "close_upstream", None)
        if callable(force_close):
            force_close()
        close = getattr(sync_gen, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


@app.post("/ask/stream")
async def ask_stream_endpoint(req: AskRequest, request: Request):
    """Answer a question with streaming response (abortable via client disconnect)."""
    history = [{"role": m.role, "content": m.content} for m in (req.chat_history or [])]
    try:
        generator = ask(
            req.question,
            chat_history=history,
            top_k=req.top_k,
            stream=True,
            response_mode=req.response_mode or "answer",
        )
        return StreamingResponse(
            _cancellable_stream(generator, request),
            media_type="text/plain",
        )
    except Exception as e:
        logger.exception("ask/stream failed")
        raise _http_error(500, f"Ask stream failed: {e}") from e


@app.post("/research")
async def research_endpoint(req: ResearchRequest, request: Request):
    """Generate a streaming research summary (abortable via client disconnect)."""
    try:
        generator = research_summary(req.topic, top_k=req.top_k)
        return StreamingResponse(
            _cancellable_stream(generator, request),
            media_type="text/plain",
        )
    except Exception as e:
        logger.exception("research failed")
        raise _http_error(500, f"Research failed: {e}") from e
