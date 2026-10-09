"""
Document Ingestion Module
Handles PDFs, Word docs, Web URLs, CSV/JSON into LangChain Documents
"""

import os
import io
import json
import logging
import threading
import requests
import pandas as pd
from pathlib import Path
from typing import Callable, List, Optional
from urllib.parse import urlparse

# Optional hooks: cancel_check() raises; progress_cb(done, total, detail)
CancelCheck = Optional[Callable[[], None]]
ProgressCb = Optional[Callable[[int, int, str], None]]

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from bs4 import BeautifulSoup

from backend.text_quality import is_readable_text

logger = logging.getLogger(__name__)

# ── Text splitter config ────────────────────────────────────────────────────
SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=800,
    chunk_overlap=100,
    separators=["\n\n", "\n", ". ", " ", ""],
)

PDF_OCR_ENABLED = os.getenv("PDF_OCR_ENABLED", "true").lower() == "true"
PDF_OCR_MIN_CHARS = int(os.getenv("PDF_OCR_MIN_CHARS", "50"))


# ── Individual loaders ───────────────────────────────────────────────────────

def _ocr_page(page) -> str:
    """Run Tesseract OCR on a PDF page rendered as an image."""
    import pytesseract
    from PIL import Image

    pix = page.get_pixmap(dpi=200)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    return pytesseract.image_to_string(img).strip()


def load_pdf(
    file_path: str,
    cancel_check: CancelCheck = None,
    progress_cb: ProgressCb = None,
) -> List[Document]:
    """Load and chunk a PDF — text extraction with OCR fallback for scanned pages."""
    import fitz  # pymupdf

    docs = []
    text_pages = 0
    ocr_pages = 0
    empty_pages = 0
    garbage_pages = 0

    # Suppress MuPDF C-level stderr spam for broken newspaper PDFs
    fitz.TOOLS.mupdf_warnings(False)

    pdf = fitz.open(file_path)
    total_pages = len(pdf)

    for page_num, page in enumerate(pdf):
        if cancel_check:
            cancel_check()
        if progress_cb:
            progress_cb(
                page_num,
                total_pages,
                f"Parsing page {page_num + 1}/{total_pages}…",
            )

        text = page.get_text("text").strip()
        extraction = "text"

        if len(text) < PDF_OCR_MIN_CHARS and PDF_OCR_ENABLED:
            try:
                ocr_text = _ocr_page(page)
                if len(ocr_text) >= PDF_OCR_MIN_CHARS:
                    text = ocr_text
                    extraction = "ocr"
                    ocr_pages += 1
                else:
                    empty_pages += 1
                    text = ""
            except Exception as e:
                logger.warning("OCR failed on page %s of %s: %s", page_num + 1, file_path, e)
                empty_pages += 1
                text = ""
        elif text:
            text_pages += 1
        else:
            empty_pages += 1

        if text and not is_readable_text(text):
            logger.warning(
                "Skipped garbage text on page %s of %s",
                page_num + 1,
                Path(file_path).name,
            )
            garbage_pages += 1
            continue

        if text:
            docs.append(Document(
                page_content=text,
                metadata={
                    "source": file_path,
                    "type": "pdf",
                    "page": page_num + 1,
                    "filename": Path(file_path).name,
                    "extraction": extraction,
                }
            ))

    pdf.close()

    if progress_cb:
        progress_cb(total_pages, total_pages, "Chunking PDF text…")

        stats = {
        "total_pages": total_pages,
        "text_pages": text_pages,
        "ocr_pages": ocr_pages,
        "empty_pages": empty_pages,
        "garbage_pages": garbage_pages,
    }
    _pdf_stats.last = stats
    # Keep attribute for older callers/tests
    load_pdf.last_stats = stats

    chunks = SPLITTER.split_documents(docs) if docs else []
    logger.info(
        "PDF %s: %d pages — %d text, %d OCR, %d empty, %d garbage → %d chunks",
        Path(file_path).name,
        total_pages,
        text_pages,
        ocr_pages,
        empty_pages,
        garbage_pages,
        len(chunks),
    )

    return chunks


_pdf_stats = threading.local()
load_pdf.last_stats = {}


def get_pdf_ingest_message(file_path: str, chunk_count: int) -> str:
    """Build a human-readable ingest summary for PDF files."""
    stats = getattr(_pdf_stats, "last", None) or getattr(load_pdf, "last_stats", {})
    if not stats:
        return f"Successfully ingested {Path(file_path).name} into {chunk_count} chunks."

    msg = (
        f"Successfully ingested {Path(file_path).name} into {chunk_count} chunks. "
        f"Pages: {stats.get('total_pages', 0)} total, "
        f"{stats.get('text_pages', 0)} text-extracted, "
        f"{stats.get('ocr_pages', 0)} OCR, "
        f"{stats.get('garbage_pages', 0)} garbage skipped."
    )
    if chunk_count == 0:
        msg += (
            " Warning: 0 chunks — file may be empty or OCR unavailable. "
            "Install Tesseract: brew install tesseract poppler"
        )
    return msg


def load_docx(
    file_path: str,
    cancel_check: CancelCheck = None,
    progress_cb: ProgressCb = None,
) -> List[Document]:
    """Load and chunk a Word document."""
    from docx import Document as WordDoc

    if cancel_check:
        cancel_check()
    if progress_cb:
        progress_cb(0, 1, "Reading Word document…")

    doc = WordDoc(file_path)
    full_text = "\n\n".join(
        para.text.strip() for para in doc.paragraphs if para.text.strip()
    )
    raw_doc = Document(
        page_content=full_text,
        metadata={
            "source": file_path,
            "type": "docx",
            "filename": Path(file_path).name,
        }
    )
    if cancel_check:
        cancel_check()
    if progress_cb:
        progress_cb(1, 1, "Chunking Word document…")
    return SPLITTER.split_documents([raw_doc])


def load_url(
    url: str,
    cancel_check: CancelCheck = None,
    progress_cb: ProgressCb = None,
) -> List[Document]:
    """Fetch a web page and extract readable text."""
    if cancel_check:
        cancel_check()
    if progress_cb:
        progress_cb(0, 2, "Downloading page…")

    headers = {"User-Agent": "Mozilla/5.0 (compatible; RAGBot/1.0)"}
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
    except requests.Timeout as e:
        raise TimeoutError(f"Timed out fetching URL: {url}") from e
    except requests.RequestException as e:
        raise ConnectionError(f"Failed to fetch URL: {e}") from e

    if cancel_check:
        cancel_check()
    if progress_cb:
        progress_cb(1, 2, "Extracting text…")

    soup = BeautifulSoup(resp.text, "html.parser")
    # Remove noise
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    title = soup.title.string.strip() if soup.title else urlparse(url).netloc
    text = soup.get_text(separator="\n", strip=True)

    raw_doc = Document(
        page_content=text,
        metadata={
            "source": url,
            "type": "url",
            "title": title,
        }
    )
    if cancel_check:
        cancel_check()
    if progress_cb:
        progress_cb(2, 2, "Chunking page text…")
    return SPLITTER.split_documents([raw_doc])


def load_csv(
    file_path: str,
    cancel_check: CancelCheck = None,
    progress_cb: ProgressCb = None,
) -> List[Document]:
    """Load a CSV file — each row becomes a document chunk."""
    if cancel_check:
        cancel_check()
    if progress_cb:
        progress_cb(0, 1, "Reading CSV…")

    df = pd.read_csv(file_path)
    docs = []
    total = len(df)
    for idx, row in df.iterrows():
        if cancel_check and int(idx) % 50 == 0:
            cancel_check()
        if progress_cb and total and int(idx) % max(1, total // 20) == 0:
            progress_cb(int(idx), total, f"Reading CSV row {int(idx) + 1}/{total}…")
        row_text = "\n".join(f"{col}: {val}" for col, val in row.items())
        docs.append(Document(
            page_content=row_text,
            metadata={
                "source": file_path,
                "type": "csv",
                "filename": Path(file_path).name,
                "row": int(idx),
            }
        ))
    if progress_cb:
        progress_cb(total or 1, total or 1, "CSV loaded")
    return docs  # rows are already small; no further splitting needed


def load_json(
    file_path: str,
    cancel_check: CancelCheck = None,
    progress_cb: ProgressCb = None,
) -> List[Document]:
    """Load a JSON file — handles list-of-objects or nested dicts."""
    if cancel_check:
        cancel_check()
    if progress_cb:
        progress_cb(0, 1, "Reading JSON…")

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = [data]
    else:
        items = [{"value": str(data)}]

    docs = []
    for idx, item in enumerate(items):
        if cancel_check and idx % 25 == 0:
            cancel_check()
        text = json.dumps(item, indent=2, ensure_ascii=False)
        docs.append(Document(
            page_content=text,
            metadata={
                "source": file_path,
                "type": "json",
                "filename": Path(file_path).name,
                "item_index": idx,
            }
        ))
    if progress_cb:
        progress_cb(1, 1, "Chunking JSON…")
    return SPLITTER.split_documents(docs)


# ── Router ───────────────────────────────────────────────────────────────────

def ingest_file(
    file_path: str,
    cancel_check: CancelCheck = None,
    progress_cb: ProgressCb = None,
) -> List[Document]:
    """Route a file path to the right loader based on extension."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {file_path}")

    ext = path.suffix.lower()
    loaders = {
        ".pdf":  load_pdf,
        ".docx": load_docx,
        ".doc":  load_docx,
        ".csv":  load_csv,
        ".json": load_json,
    }
    loader = loaders.get(ext)
    if loader is None:
        raise ValueError(f"Unsupported file type: {ext}")
    return loader(file_path, cancel_check=cancel_check, progress_cb=progress_cb)


def ingest_source(
    source: str,
    cancel_check: CancelCheck = None,
    progress_cb: ProgressCb = None,
) -> List[Document]:
    """
    Auto-detect whether the source is a URL or file path,
    then route to the right loader.
    """
    parsed = urlparse(source)
    if parsed.scheme in ("http", "https"):
        return load_url(source, cancel_check=cancel_check, progress_cb=progress_cb)
    if os.path.isfile(source):
        return ingest_file(source, cancel_check=cancel_check, progress_cb=progress_cb)
    raise FileNotFoundError(f"Source not found or unsupported: {source}")
