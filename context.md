# RAG context for agents

Last updated: 2026-10-08.

Read this file before changing the repo. It is the source of truth for how the system is built. Chat history is not. The human-facing setup guide is [README.md](README.md). The system design is [ARCHITECTURE.md](ARCHITECTURE.md). There is no other README. Do not add `frontend/README.md`.

After a behavior, path, env, or API change, update this file, ARCHITECTURE.md, and README together.

## What this project is

This is a single-user, fully local RAG app. The user ingests documents, ChromaDB stores embeddings, and Ollama writes Chat and Research answers.

Intended use is one person on one machine, with the API bound to `127.0.0.1`. There is no auth, no multi-tenant isolation, and no cloud inference API.

Primary content is user documents, especially newspaper and scanned PDFs.

## Rules that are easy to get wrong

- Embeddings retrieve. The LLM only generates Chat and Research text. Ingest does not call Ollama.
- Changing `OLLAMA_MODEL` does not require re-ingest. Changing `EMBEDDING_MODEL` does. Wipe `local_data/chroma_db` and ingest again.
- Keep `OLLAMA_THINK=false` unless the user asks otherwise. Qwen thinking tokens are not streamed as answer text, so the UI sits on "Thinking…".
- Keep `API_HOST=127.0.0.1`. Do not add a public bind or a second frontend README.
- Do not commit `.env`, `local_data/`, virtualenvs, `node_modules`, `frontend/dist`, or `.idea/`.
- Active data directory is `local_data/`. A root `data/` folder is legacy and should not be reintroduced.
- Python 3.13 generator objects cannot take arbitrary attributes. Ollama streams are wrapped in `_ClosableStream` in `backend/rag_chain.py`. Do not `setattr` a `close_upstream` function onto a raw generator.

## Stack

| Layer | Choice |
|-------|--------|
| UI | React 19, Vite, TypeScript, CSS modules, Framer Motion. Port 5173, host `127.0.0.1`. |
| API | FastAPI + Uvicorn. Port 8000. Routes live in `backend/api.py`. |
| Vectors | ChromaDB through LangChain `Chroma` in `backend/vector_store.py`. |
| Embeddings | `BAAI/bge-small-en-v1.5` via sentence-transformers. Device from `EMBEDDING_DEVICE` (`auto`, `cuda`, `cpu`, `mps`). |
| LLM | Ollama chat API. Model from `OLLAMA_MODEL`. |
| PDF | PyMuPDF, then Tesseract OCR when a page is short and `PDF_OCR_ENABLED` is true. |
| Other loaders | python-docx, pandas, BeautifulSoup. |
| Logs | Rotating file at `local_data/logs/rag.log`. |

`uvloop` is marked `sys_platform != "win32"` in `requirements.txt`. Windows launchers are `start.bat` / `stop.bat` (they call the `.ps1` files). Unix launchers are `start.sh` / `stop.sh`.

## Repository map

```
backend/api.py            HTTP routes, upload save, cancellable streams
backend/rag_chain.py      retrieve, ask, research, Ollama streaming
backend/ingestion.py      loaders, OCR, 800-char chunks, 100 overlap
backend/ingest_jobs.py    background jobs, progress, cancel, filename and URL checks
backend/vector_store.py   embedder singleton, Chroma, search, list/delete sources
backend/text_quality.py   reject garbage OCR text
backend/logging_config.py
frontend/src/App.tsx      shell, nav, stats polling
frontend/src/api/client.ts
frontend/src/views/ChatView.tsx
frontend/src/views/ResearchView.tsx
frontend/src/views/KnowledgeView.tsx   Library
frontend/src/components/  ChunkPanel, IngestProgress, Markdown
frontend/src/styles/global.css         design tokens
frontend/vite.config.ts   proxy /api -> http://127.0.0.1:8000, strip /api
scripts/ingest_cli.py
```

Put route handlers in `api.py`. Put retrieval and prompt logic in `rag_chain.py`. Put parsing in `ingestion.py`. Put job lifecycle in `ingest_jobs.py`. Put Chroma access in `vector_store.py`.

## UI

The current look is the Botanical style: warm paper, forest ink, serif titles.

Tokens in `frontend/src/styles/global.css`:

| Token | Value | Use |
|-------|--------|-----|
| `--paper` | `#F9F8F4` | page background |
| `--ink` | `#2D3A31` | text and filled buttons |
| `--ink-soft` | `#4A5744` | secondary text, outlines |
| `--paper-elevated` | `#FFFFFF` | cards |
| `--line` | `#E4E1D8` | borders |
| `--font-display` | Playfair Display | headings and the RAG wordmark |
| `--font-body` | Source Sans 3 | UI copy |

Primary actions are pill buttons: forest fill, white label, slight letter-spacing, uppercase. Secondary actions are outline pills. Do not introduce a dark theme, cyan dashboard, or a second accent color unless the user asks for a redesign.

Fonts are loaded in `frontend/index.html`. `MotionConfig reducedMotion="user"` wraps the app. Hover changes color or border, not layout. Form fields that only have a placeholder also have a real `<label>` (visually hidden via `.sr-only` when the chrome is a composer).

Views:

- Chat: Answer vs List matches, chunk slider, Debug, Clear, Stop, streaming markdown. The transcript stays when you open Research or Library, until refresh or Clear.
- Research: topic, chunk slider, Stop, markdown report, download `.md`. The report stays when you leave the screen, until refresh or a new run.
- Library: file drop, URL ingest, job progress, sources table.

All three screens stay mounted. The shell hides the inactive ones so their state is not discarded.

Debug chunks use `localStorage` key `rag.debugChunks`.

## Request flows

**Chat.** The UI calls `POST /ask/stream` only. The backend retrieves once, then streams Ollama. With Debug on, the UI first calls `POST /retrieve` so the chunk panel can render. `response_mode` is `answer` or `list`. List mode uses a higher default `top_k`. Stop uses `AbortController`. The API watches client disconnect and calls `close_upstream()` on `_ClosableStream`, which closes the Ollama HTTP response even if `iter_lines()` is blocked, then closes the generator.

**Research.** `POST /research` retrieves, then streams a fixed section structure. Stop and `.md` download are client-side.

**Ingest.** The UI uses async jobs.

1. Upload filenames go through `safe_filename()` (`Path.name` only) and an allow-list: `.pdf`, `.docx`, `.doc`, `.csv`, `.json`.
2. URLs must be `http` or `https` (`validate_url`).
3. A worker thread parses, chunks, filters unreadable text, embeds, and writes Chroma under a lock.
4. PDF pages with little text try OCR. Chunks are 800 characters with 100 overlap.
5. The UI polls `GET /ingest/jobs/{id}` and cancels with `POST /ingest/jobs/{id}/cancel`.
6. Sync `POST /ingest/file` and `POST /ingest/url` remain for the CLI and simple clients.

`DELETE /sources?source=` deletes Chroma chunks for that source string. It does not delete the file on disk.

## API

| Method | Path | Notes |
|--------|------|--------|
| GET | `/health` | Tesseract, device, CUDA, torch version, log path |
| GET | `/stats` | counts plus embedding fields |
| GET | `/sources` | unique sources from chunk metadata |
| DELETE | `/sources` | query `source` |
| POST | `/ingest/file`, `/ingest/url` | sync |
| POST | `/ingest/file/jobs`, `/ingest/url/jobs` | async |
| GET | `/ingest/jobs/{id}` | status |
| POST | `/ingest/jobs/{id}/cancel` | cancel |
| POST | `/retrieve` | no LLM |
| POST | `/ask`, `/ask/stream` | `top_k` 1–100, `response_mode` |
| POST | `/research` | streaming |

`/ask/stream` and `/research` return `text/plain` streams. Job ids are UUIDs. Finished jobs are pruned so the in-memory map stays around 100 entries.

## Environment

```
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3.5:9b
OLLAMA_THINK=false
OLLAMA_TIMEOUT=300
CHROMA_DB_PATH=./local_data/chroma_db
UPLOAD_DIR=./local_data/uploads
EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
EMBEDDING_DEVICE=auto
MIN_RELEVANCE_SCORE=0.25
PDF_OCR_ENABLED=true
LOG_DIR=./local_data/logs
LOG_LEVEL=INFO
LOG_TO_CONSOLE=false
API_HOST=127.0.0.1
API_PORT=8000
FRONTEND_PORT=5173
```

Read config with `os.getenv` and a default. Do not hardcode a device or model in new code.

## Process startup

`start.bat` calls `start.ps1`. `./start.sh` is the Unix equivalent. Both:

1. Exit if `.env` is missing, then export its keys into the process environment.
2. Create `UPLOAD_DIR`, `CHROMA_DB_PATH`, and `LOG_DIR`. Fail if the Chroma directory is not writable.
3. Call the stop script so ports `API_PORT` and `FRONTEND_PORT` are free.
4. Prefer `.venv/Scripts/python.exe` (Windows) or the active interpreter, and run `python -m uvicorn backend.api:app --host API_HOST --port API_PORT --reload` from the repo root.
5. Poll `http://127.0.0.1:$API_PORT/health` for about 20 seconds.
6. Run `npm run dev -- --host 127.0.0.1 --port $FRONTEND_PORT` with working directory `frontend/`.
7. On Ctrl+C, kill both process trees and run the stop script again.

`stop.ps1` / `stop.sh` kill `uvicorn backend.api:app` for this app, listeners on the API port, the frontend port, and legacy Streamlit port 8501. They do not kill unrelated `npm run dev` processes in other projects.

The first API request that embeds text loads the sentence-transformers weights. That can take a few seconds and is normal. Ollama must already be running. These scripts do not start it.

## How to run and inspect

```powershell
.\start.bat
.\stop.bat
```

```bash
./start.sh
./stop.sh
```

```bash
python scripts/ingest_cli.py path/to/docs/
python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
```

Logs: `local_data/logs/rag.log`.

Manual wipe of the index: stop the app, delete the contents of `local_data/chroma_db`, start again, re-ingest. There is no wipe endpoint.

## Status

Done:

- Chat, Research, and Library UI
- Multi-format ingest, OCR, garbage-text filter
- Async ingest with progress and cancel
- Embedding device selection and relevance cutoff
- Abortable Ollama streams via `_ClosableStream`
- Windows and Unix start/stop scripts
- CLI bulk ingest

Fragile:

- `uvicorn --reload` can race Chroma and make `/stats` return 500. Restart. If it persists, wipe the index.
- MuPDF and Uvicorn access lines still hit the terminal. App logs go to `rag.log`.
- A CPU PyTorch wheel (`+cpu`) ignores the GPU until a CUDA wheel is installed.
- List mode sends a large prompt. It is slow when the chunk slider is high.

Not built:

- Wipe-KB API or Library control
- Log viewer
- Auth or multi-user
- LLM picker in the UI
- Per-batch embed progress inside Chroma `add_documents`
- Automated tests

## Open work, in order

1. Wipe knowledge base from the API and Library, with a confirmation step.
2. Make the Chroma path absolute and avoid reload races.
3. Send OCR and ingest detail to `rag.log`.
4. Document a move to `bge-base` that includes a required re-ingest.
5. Optional Ollama model picker.
6. Harden the API before any non-loopback bind: restrict CORS to the Vite origin, cap upload and prompt size, reject non-public URL ingest targets and re-check redirects, return short client errors while logging the exception.

## Known issues

| Symptom | Why | What to do |
|---------|-----|------------|
| Embeddings stay on CPU | CPU torch wheel | CUDA wheel, `EMBEDDING_DEVICE=auto`, restart API |
| Chat stuck on Thinking | Qwen thinking tokens | `OLLAMA_THINK=false`, restart, or Stop |
| List mode is slow | Large prompt | Lower the chunk slider, keep think off |
| Poor answers | Bad PDF text or low scores | OCR re-ingest, Debug panel, tune `MIN_RELEVANCE_SCORE` |
| OCR does nothing | Tesseract missing from PATH | Install, restart, re-upload |
| `/stats` 500 | Chroma plus `--reload` | Restart; wipe chroma if it continues |
| Port already in use | Old API or Vite | `.\stop.bat` or `./stop.sh` |
| Retrieval looks random after a model change | Embedding model changed | Wipe chroma and re-ingest |

## When you change something

1. Match the existing module split.
2. Keep streams cancellable.
3. Preserve Botanical tokens unless the user asks for a new visual system.
4. Update the date, status, and env or API tables in this file.
5. Update README when a user-visible command, port, or feature changes.
