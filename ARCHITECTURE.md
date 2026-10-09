# RAG architecture

How the running system is shaped. Setup commands live in [README.md](README.md). Module rules for edits live in [context.md](context.md).

## Purpose

This system answers questions and writes research notes from a private document library. It is a single-user process on one machine. The browser, the API, the vector index, and the language model all stay local.

The design splits **finding evidence** from **writing prose**. An embedding model and ChromaDB do the first job. Ollama does the second. Ingest never calls the language model. A chat answer is only as good as the chunks retrieval returned.

## Runtime shape

Four participants. The start script launches two of them.

```
┌──────────────────────────────────────────────────────────┐
│  Browser                                                 │
│  React UI  (Chat, Research, Library)                     │
└───────────────────────────┬──────────────────────────────┘
                            │  HTTP  /api/*   only
┌───────────────────────────▼──────────────────────────────┐
│  Vite dev server          127.0.0.1:5173                 │
│  Serves the UI. Proxies /api/* to the API and strips     │
│  the /api prefix.                                        │
└───────────────────────────┬──────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────┐
│  FastAPI                  127.0.0.1:8000                 │
│  One process. Request handlers plus background ingest    │
│  threads inside that process.                            │
└───────────────┬───────────────────────┬──────────────────┘
                │                       │
                │ read/write            │ HTTP, Chat and Research only
┌───────────────▼──────────┐   ┌────────▼─────────────────┐
│  ChromaDB                │   │  Ollama                  │
│  local_data/chroma_db    │   │  127.0.0.1:11434         │
│  collection rag_knowledge│   │  started by the user     │
└──────────────────────────┘   └──────────────────────────┘
```

Uploaded files sit beside the index in `local_data/uploads`. Logs sit in `local_data/logs/rag.log`. None of those directories are the source of truth for code.

The UI does not open Chroma or Ollama. The API does not render HTML. Ollama does not see the raw files. It sees only the prompt the API built from retrieved chunks.

## Two indexes of the same text

| Store | What it holds | Who writes it | Who reads it |
|-------|----------------|---------------|--------------|
| Upload directory | Original file bytes | Ingest, when a file is uploaded | Loaders, once, at ingest time |
| Chroma collection `rag_knowledge_base` | Chunk text, embedding, metadata | Ingest, after chunking | Search, source list, source delete |

Search never opens the original file. Deleting a source removes vectors whose metadata `source` matches that string. The file on disk remains.

Metadata on a chunk is small and stable: `source` (path or URL), `type`, `filename` or `title`, and sometimes `page`, `row`, or `item_index`. The Library table is a distinct list of those `source` values, not a separate database.

## Component boundaries

| Module | Owns | Does not own |
|--------|------|----------------|
| `frontend/src/views/*` | Screens, streaming display, job polling | Retrieval math, prompts, file parsing |
| `frontend/src/api/client.ts` | HTTP to `/api` | Knowledge of Chroma or Ollama |
| `backend/api.py` | Routes, upload save, bridging sync streams to async responses | Chunking, embeddings, prompt text |
| `backend/ingest_jobs.py` | Job records, worker threads, cancel flags, filename and URL checks | How a PDF is parsed |
| `backend/ingestion.py` | Format loaders, OCR, splitting | Where vectors are stored |
| `backend/text_quality.py` | Rejecting unreadable text | Deciding relevance at query time |
| `backend/vector_store.py` | Embedder singleton, Chroma client, search, source delete | Prompts and Ollama |
| `backend/rag_chain.py` | Retrieve, prompts, Ollama chat | HTTP status codes |

Sync ingest (`POST /ingest/file`, `POST /ingest/url`) and `scripts/ingest_cli.py` call the same loaders and `add_documents()`. They skip the job table. The CLI is another client of the library, not a second pipeline.

## Ingest path

```
Library UI or CLI
        │
        ▼
Save upload  or  fetch URL
        │
        ▼
Worker thread (UI jobs)  or  request thread (sync / CLI)
        │
        ├─ parse by type
        │    PDF: PyMuPDF text; OCR if the page is shorter than PDF_OCR_MIN_CHARS
        │    DOCX, CSV, JSON, HTML: dedicated loader
        ├─ drop text that fails is_readable_text()
        ├─ split to ~800 characters, 100 overlap
        ▼
add_documents()  under the Chroma write lock
        │
        ▼
Vectors + metadata in local_data/chroma_db
```

UI jobs are in-memory. Each job has a UUID, a status, and a cancel event. The browser polls `GET /ingest/jobs/{id}` about every 400 ms. Cancel sets the event. The worker checks it between pages and before the Chroma write. Jobs are not durable: restarting the API forgets them. The vectors already written remain.

The embedding model is a process-wide singleton. The first encode loads weights, which is why the first ingest or search after startup is slow. Documents are embedded without the BGE query prefix. Queries are embedded with that prefix so short questions match passage-style vectors.

Writes to Chroma take `_store_lock`. Reads for search do not. `uvicorn --reload` can restart the process while a write is in flight, which is why `/stats` sometimes fails until restart.

## Query path

Chat and Research share one retrieval function, `search()`:

1. Embed the question with the BGE prefix.
2. Ask Chroma for more neighbors than `top_k` (`max(top_k * 3, top_k)`).
3. Drop scores below `MIN_RELEVANCE_SCORE`.
4. Return at most `top_k` pairs of `(chunk, score)`.

`POST /retrieve` stops there and returns the chunks. Chat and Research continue.

```
Browser
  │  POST /ask/stream   or   POST /research
  ▼
FastAPI
  │  search() once
  ▼
Prompt = system instructions + prior chat turns + labeled chunks + question
  │
  ▼
Ollama /api/chat   stream=true, think=OLLAMA_THINK
  │
  ▼
text/plain token stream back through Vite to the page
```

Chat has two prompt shapes, chosen by `response_mode`:

- `answer` synthesizes a reply and is told to cite sources.
- `list` is told to enumerate matches and not compress them. The UI raises the default `top_k` for that mode.

Research uses a third prompt that demands overview, findings, analysis, sources, and gaps. It does not use chat history.

If search returns nothing, the API streams a fixed "nothing in the library" message and does not call Ollama.

Debug in Chat is a side path. The page calls `/retrieve` first so it can render the chunk panel, then still calls `/ask/stream`, which searches again. The answer does not depend on the debug call.

## Streaming and cancel

Ollama is a blocking HTTP stream inside a worker thread (`iterate_in_threadpool`). The browser reads `text/plain` and appends deltas.

Stop is a disconnect. The API then:

1. Calls `close_upstream()` on the stream wrapper, which closes the Ollama response even if `iter_lines()` is blocked.
2. Closes the generator so its `finally` runs.

The wrapper exists because a Python generator cannot carry an extra method. `_ClosableStream` in `rag_chain.py` is the object the API holds.

Ingest cancel is different. It is a flag on the job, not a closed socket. The worker must reach its next cancel check.

## What is in memory vs on disk

| Data | Lifetime |
|------|----------|
| Chroma collection, upload files, `rag.log` | Disk, survives restart |
| Embedding model and Chroma client | Process singletons, rebuilt on restart |
| Ingest job list | RAM only, capped near 100 finished jobs |
| Chat transcript | Browser state inside the Chat screen. Switching to Research or Library keeps it until refresh or Clear. Sent back as `chat_history` on the next ask. Not stored server-side |
| Research report | Browser state inside the Research screen. Switching views keeps it until refresh or a new report |

There is one collection, `rag_knowledge_base`. There are no users, sessions, or per-library namespaces on the server.

## What this architecture leaves out

- No authentication. Safety is the loopback bind.
- No second copy of the index and no replication.
- No queue service. Ingest threads live inside the API process.
- No tool-calling or multi-step agent loop. One retrieval, then one generation.
- No re-ranking model after the similarity cutoff.
- Deleting a source does not delete the uploaded file, and there is no API to wipe the whole index.
