"""
RAG Chain Module
Retrieval-augmented generation pipeline using a local Ollama model and ChromaDB.
100% local — no API keys, no internet required after setup.
Supports both single Q&A and streaming research summaries.
"""

import os
import requests
import json
from typing import Callable, Generator, Iterator, List
from langchain_core.documents import Document
from backend.vector_store import search

# ── Config ────────────────────────────────────────────────────────────────────
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
MODEL = os.getenv("OLLAMA_MODEL", "llama3")   # change to llama3:70b, mistral, etc.
TOP_K = 6  # chunks to retrieve per query
# Qwen3.x "thinking" can delay first visible token for a long time (esp. List mode).
# Default off for snappy RAG; set OLLAMA_THINK=true to enable.
OLLAMA_THINK = os.getenv("OLLAMA_THINK", "false").lower() in ("1", "true", "yes")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "300"))


# ── Prompt builders ───────────────────────────────────────────────────────────

def build_context(chunks: List[tuple]) -> str:
    """Format retrieved chunks into a structured context block."""
    sections = []
    for i, (doc, score) in enumerate(chunks, 1):
        src = doc.metadata.get("filename") or doc.metadata.get("title") or doc.metadata.get("source", "Unknown")
        src_type = doc.metadata.get("type", "?").upper()
        page = doc.metadata.get("page")
        page_info = f" | page {page}" if page else ""
        sections.append(
            f"[SOURCE {i} | {src_type} | {src}{page_info} | relevance: {score:.2f}]\n{doc.page_content}"
        )
    return "\n\n---\n\n".join(sections)


def format_retrieved_chunks(chunks: List[tuple]) -> List[dict]:
    """Format chunks for API/UI display."""
    result = []
    for i, (doc, score) in enumerate(chunks, 1):
        result.append({
            "index": i,
            "content": doc.page_content,
            "score": round(float(score), 3),
            "source": doc.metadata.get("filename") or doc.metadata.get("title") or doc.metadata.get("source", "Unknown"),
            "type": doc.metadata.get("type", "?"),
            "page": doc.metadata.get("page"),
        })
    return result


SYSTEM_PROMPT = """You are a knowledgeable research assistant with access to a curated knowledge base. \
Your job is to answer questions accurately and thoroughly using the provided context.

Rules:
- Base your answers primarily on the provided context chunks.
- If the context is insufficient, say so clearly and offer what you do know.
- Always cite which sources you used (e.g. "According to SOURCE 2...").
- For research summaries, organize your response with clear structure: key findings, details, and conclusions.
- Be concise but comprehensive. Prefer bullet points for lists of facts.
- If sources contradict each other, acknowledge the discrepancy.
"""

LIST_SYSTEM_PROMPT = """You are a data extraction assistant with access to a curated knowledge base. \
Your job is to list ALL matching items found in the context — not summarize them.

Rules:
- Return EVERY distinct matching item as a bullet point.
- Do NOT merge, summarize, or compress multiple items into one line.
- Group bullets by source when possible (e.g. under each SOURCE heading).
- Include all relevant data points: names, numbers, dates, entries, and records.
- If the same item appears in multiple sources, list it under each source.
- If no matches are found, say so clearly.
- Do not add information not present in the context.
"""


# ── Retrieval ─────────────────────────────────────────────────────────────────

def retrieve(question: str, top_k: int = TOP_K) -> dict:
    """Retrieve relevant chunks without calling the LLM."""
    from backend.vector_store import MIN_RELEVANCE_SCORE

    chunks = search(question, k=top_k)
    scores = [c[1] for c in chunks]
    avg_score = sum(scores) / len(scores) if scores else 0.0

    warning = None
    if not chunks:
        warning = (
            f"No chunks matched your question above relevance threshold "
            f"({MIN_RELEVANCE_SCORE}). Try rephrasing or re-ingesting documents."
        )
    elif max(scores) < 0.4:
        warning = (
            "Low match quality — retrieved chunks may not be very relevant. "
            "Check the chunks below or re-ingest with Tesseract OCR."
        )

    return {
        "chunks": format_retrieved_chunks(chunks),
        "count": len(chunks),
        "min_relevance_score": MIN_RELEVANCE_SCORE,
        "avg_score": round(avg_score, 3),
        "warning": warning,
    }


# ── Core QA function ──────────────────────────────────────────────────────────

def ask(
    question: str,
    chat_history: List[dict] = None,
    top_k: int = TOP_K,
    stream: bool = False,
    response_mode: str = "answer",
) -> str | Generator:
    """
    Main RAG entry point.
    - Retrieves relevant chunks from ChromaDB
    - Builds a prompt with context
    - Calls Ollama and returns the answer

    Args:
        question: User's question
        chat_history: List of {"role": "user"/"assistant", "content": "..."} dicts
        top_k: Number of context chunks to retrieve
        stream: If True, returns a generator yielding text deltas
        response_mode: "answer" for summary, "list" to enumerate all matches

    Returns:
        Full answer string (stream=False) or text delta generator (stream=True)
    """
    chunks = search(question, k=top_k)

    if not chunks:
        no_context_msg = (
            "I couldn't find relevant information in the knowledge base for your question. "
            "Please make sure you've ingested relevant documents first, or rephrase your query."
        )
        if stream:
            def _empty():
                yield no_context_msg
            return _empty()
        return no_context_msg

    context = build_context(chunks)
    history = chat_history or []

    if response_mode == "list":
        user_message = f"""Using the following context from the knowledge base, list ALL items that match this question.

Instructions:
- Return every distinct matching item as a bullet point.
- Group by source. Do not summarize — enumerate everything found.

<context>
{context}
</context>

<question>
{question}
</question>"""
        system_prompt = LIST_SYSTEM_PROMPT
    else:
        user_message = f"""Using the following context from the knowledge base, answer this question:

<context>
{context}
</context>

<question>
{question}
</question>"""
        system_prompt = SYSTEM_PROMPT

    messages = history + [{"role": "user", "content": user_message}]

    if stream:
        return _stream_response(messages, system_prompt=system_prompt)
    return _sync_response(messages, system_prompt=system_prompt)


def _ollama_payload(messages: List[dict], system_prompt: str, stream: bool) -> dict:
    return {
        "model": MODEL,
        "messages": [{"role": "system", "content": system_prompt}] + messages,
        "stream": stream,
        # Native Ollama API — disables hidden reasoning for thinking models (qwen3.x)
        "think": OLLAMA_THINK,
    }


def _sync_response(messages: List[dict], system_prompt: str = SYSTEM_PROMPT) -> str:
    """Non-streaming Ollama call."""
    payload = _ollama_payload(messages, system_prompt, stream=False)
    resp = requests.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        json=payload,
        timeout=OLLAMA_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"]


class _ClosableStream:
    """Iterator over Ollama deltas. Generators cannot carry extra attributes."""

    def __init__(self, generator: Generator, close_upstream: Callable[[], None]) -> None:
        self._generator = generator
        self.close_upstream = close_upstream

    def __iter__(self) -> "_ClosableStream":
        return self

    def __next__(self) -> str:
        return next(self._generator)

    def close(self) -> None:
        self.close_upstream()
        self._generator.close()


def _stream_response(messages: List[dict], system_prompt: str = SYSTEM_PROMPT) -> Iterator[str]:
    """
    Streaming Ollama call — yields text deltas from message.content.
    Closes the upstream Ollama connection on GeneratorExit (client abort / Stop).
    `.close_upstream()` lets callers force-close even while blocked on read.
    """
    payload = _ollama_payload(messages, system_prompt, stream=True)
    resp = requests.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        json=payload,
        stream=True,
        timeout=OLLAMA_TIMEOUT,
    )

    def close_upstream() -> None:
        try:
            resp.close()
        except Exception:
            pass

    def gen() -> Generator:
        try:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                msg = chunk.get("message") or {}
                # Prefer visible answer tokens; thinking is separate when think=true
                delta = msg.get("content") or ""
                if delta:
                    yield delta
                if chunk.get("done"):
                    break
        except GeneratorExit:
            raise
        finally:
            close_upstream()

    # Generator objects have no __dict__ (Python 3.13 rejects setattr on them).
    return _ClosableStream(gen(), close_upstream)


# ── Research summarizer ───────────────────────────────────────────────────────

def research_summary(topic: str, top_k: int = 10) -> Generator:
    """
    Generate a structured research summary on a topic from the knowledge base.
    Always streams.
    """
    chunks = search(topic, k=top_k)

    if not chunks:
        def _empty():
            yield f"No information found on '{topic}' in the knowledge base."
        return _empty()

    context = build_context(chunks)

    summary_prompt = f"""Generate a comprehensive research summary on the following topic \
using the provided knowledge base context.

Structure your response as:
## Overview
A brief paragraph summarizing what the knowledge base contains on this topic.

## Key Findings
Bullet points of the most important facts, insights, or data points.

## Details & Analysis
Deeper dive into the content, organized by subtopic if applicable.

## Sources Used
List the sources you drew from.

## Gaps & Limitations
What the knowledge base does NOT cover well on this topic.

---
Topic: {topic}

<context>
{context}
</context>"""

    return _stream_response([{"role": "user", "content": summary_prompt}])
