"""
Vector Store Module
ChromaDB + sentence-transformers for fully local embeddings & retrieval.
"""

import os
import logging
import threading
from typing import List, Tuple
from pathlib import Path

import chromadb
from chromadb.config import Settings
from langchain_core.documents import Document
from langchain_community.vectorstores import Chroma
from langchain_community.embeddings import HuggingFaceEmbeddings

logger = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────────
CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH", "./data/chroma_db")
COLLECTION_NAME = "rag_knowledge_base"
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
MIN_RELEVANCE_SCORE = float(os.getenv("MIN_RELEVANCE_SCORE", "0.25"))
# auto | cuda | cpu | mps  (auto picks cuda → mps → cpu)
EMBEDDING_DEVICE = os.getenv("EMBEDDING_DEVICE", "auto").strip().lower()
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

_resolved_device: str | None = None


def resolve_embedding_device(prefer: str | None = None) -> str:
    """
    Pick torch device for sentence-transformers embeddings.
    Note: requires a CUDA-enabled PyTorch wheel for GPU (not torch+cpu).
    """
    global _resolved_device
    if _resolved_device is not None and prefer is None:
        return _resolved_device

    choice = (prefer or EMBEDDING_DEVICE or "auto").strip().lower()
    try:
        import torch
    except ImportError:
        logger.warning("torch not available — embeddings will use CPU")
        _resolved_device = "cpu"
        return _resolved_device

    if choice in ("cuda", "gpu"):
        if torch.cuda.is_available():
            _resolved_device = "cuda"
        else:
            logger.warning(
                "EMBEDDING_DEVICE=%s but CUDA is unavailable "
                "(torch=%s). Falling back to CPU. "
                "Install a CUDA PyTorch build if you have an NVIDIA GPU.",
                choice,
                getattr(torch, "__version__", "?"),
            )
            _resolved_device = "cpu"
    elif choice == "mps":
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            _resolved_device = "mps"
        else:
            logger.warning("EMBEDDING_DEVICE=mps unavailable — using CPU")
            _resolved_device = "cpu"
    elif choice == "cpu":
        _resolved_device = "cpu"
    else:
        # auto
        if torch.cuda.is_available():
            _resolved_device = "cuda"
        elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            _resolved_device = "mps"
        else:
            _resolved_device = "cpu"
            if "+cpu" in getattr(torch, "__version__", ""):
                logger.info(
                    "PyTorch is CPU-only (%s). Embeddings use CPU. "
                    "For NVIDIA GPU: pip install torch --index-url "
                    "https://download.pytorch.org/whl/cu124",
                    torch.__version__,
                )

    return _resolved_device


class RAGEmbeddings(HuggingFaceEmbeddings):
    """Embeddings with BGE query prefix for better retrieval."""

    def embed_query(self, text: str) -> List[float]:
        if "bge" in self.model_name.lower():
            text = BGE_QUERY_PREFIX + text
        return super().embed_query(text)


# ── Singleton embedder ────────────────────────────────────────────────────────
_embedder: RAGEmbeddings | None = None

def get_embedder() -> RAGEmbeddings:
    global _embedder
    if _embedder is None:
        device = resolve_embedding_device()
        logger.info(
            "Loading embedding model: %s on device=%s",
            EMBEDDING_MODEL,
            device,
        )
        _embedder = RAGEmbeddings(
            model_name=EMBEDDING_MODEL,
            model_kwargs={"device": device},
            encode_kwargs={"normalize_embeddings": True},
        )
    return _embedder


# ── Singleton vector store ────────────────────────────────────────────────────
_vector_store: Chroma | None = None
_store_lock = threading.Lock()

def get_vector_store() -> Chroma:
    global _vector_store
    if _vector_store is None:
        with _store_lock:
            if _vector_store is None:
                Path(CHROMA_DB_PATH).mkdir(parents=True, exist_ok=True)
                _vector_store = Chroma(
                    collection_name=COLLECTION_NAME,
                    embedding_function=get_embedder(),
                    persist_directory=CHROMA_DB_PATH,
                )
    return _vector_store


# ── Public API ────────────────────────────────────────────────────────────────

def add_documents(docs: List[Document]) -> int:
    """
    Add a list of LangChain Documents to ChromaDB.
    Returns the number of chunks added.
    """
    if not docs:
        return 0
    store = get_vector_store()
    with _store_lock:
        store.add_documents(docs)
    return len(docs)


def search(query: str, k: int = 5, min_score: float | None = None) -> List[Tuple[Document, float]]:
    """
    Similarity search returning (Document, score) pairs.
    Score is cosine similarity — higher is better.
    Filters out chunks below MIN_RELEVANCE_SCORE.
    """
    threshold = MIN_RELEVANCE_SCORE if min_score is None else min_score
    store = get_vector_store()
    # Fetch extra candidates so filtering still returns up to k results
    fetch_k = max(k * 3, k)
    results = store.similarity_search_with_relevance_scores(query, k=fetch_k)

    filtered = [(doc, score) for doc, score in results if score >= threshold]
    dropped = len(results) - len(filtered)

    if dropped:
        logger.info(
            "Retrieval: kept %d/%d chunks (threshold=%.2f) for query: %.80s",
            len(filtered),
            len(results),
            threshold,
            query,
        )

    return filtered[:k]


def get_retriever(k: int = 5):
    """Return a LangChain retriever for use in chains."""
    return get_vector_store().as_retriever(
        search_type="similarity",
        search_kwargs={"k": k},
    )


def list_sources() -> List[dict]:
    """
    Return a deduplicated list of all ingested sources with metadata.
    """
    store = get_vector_store()
    collection = store._collection
    results = collection.get(include=["metadatas"])

    seen = set()
    sources = []
    for meta in results["metadatas"]:
        key = meta.get("source", "unknown")
        if key not in seen:
            seen.add(key)
            sources.append({
                "source": key,
                "type": meta.get("type", "unknown"),
                "filename": meta.get("filename") or meta.get("title") or key,
            })
    return sources


def delete_source(source: str) -> int:
    """
    Delete all chunks from a specific source.
    Returns the number of chunks deleted.
    """
    store = get_vector_store()
    with _store_lock:
        collection = store._collection
        results = collection.get(
            where={"source": source},
            include=["metadatas"],
        )
        ids = results["ids"]
        if ids:
            collection.delete(ids=ids)
    return len(ids)


def collection_stats() -> dict:
    """Return basic stats about the knowledge base."""
    store = get_vector_store()
    count = store._collection.count()
    sources = list_sources()
    device = resolve_embedding_device()
    cuda_available = False
    torch_version = None
    try:
        import torch
        cuda_available = torch.cuda.is_available()
        torch_version = getattr(torch, "__version__", None)
    except ImportError:
        pass
    return {
        "total_chunks": count,
        "total_sources": len(sources),
        "embedding_model": EMBEDDING_MODEL,
        "embedding_device": device,
        "embedding_device_preference": EMBEDDING_DEVICE,
        "cuda_available": cuda_available,
        "torch_version": torch_version,
        "min_relevance_score": MIN_RELEVANCE_SCORE,
    }
