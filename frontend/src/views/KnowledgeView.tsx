import { useCallback, useEffect, useRef, useState, type DragEvent, type FormEvent } from 'react'
import {
  ApiError,
  cancelIngestJob,
  deleteSource,
  getSources,
  startFileIngestJob,
  startUrlIngestJob,
  waitForIngestJob,
  type IngestJob,
  type SourceItem,
} from '../api/client'
import { IngestProgress } from '../components/IngestProgress'
import styles from './KnowledgeView.module.css'

const ACCEPT = '.pdf,.docx,.doc,.csv,.json'
const MAX_TOASTS = 8

type Toast = { id: string; tone: 'ok' | 'warn' | 'err'; text: string }

function errText(err: unknown, fallback: string) {
  if (err instanceof ApiError) return err.message
  if (err instanceof Error) return err.message
  return fallback
}

export function KnowledgeView({ onChanged }: { onChanged?: () => void }) {
  const [sources, setSources] = useState<SourceItem[]>([])
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const [toasts, setToasts] = useState<Toast[]>([])
  const [error, setError] = useState<string | null>(null)
  const [jobs, setJobs] = useState<IngestJob[]>([])
  const [cancellingId, setCancellingId] = useState<string | null>(null)
  const pollControllers = useRef<Map<string, AbortController>>(new Map())

  const refresh = useCallback(async () => {
    try {
      setSources(await getSources())
      setError(null)
    } catch (err) {
      setError(errText(err, 'Failed to load sources'))
    }
  }, [])

  useEffect(() => {
    void refresh()
    return () => {
      pollControllers.current.forEach((c) => c.abort())
      pollControllers.current.clear()
    }
  }, [refresh])

  function pushToast(toast: Omit<Toast, 'id'>) {
    const id = crypto.randomUUID()
    setToasts((t) => [...t, { ...toast, id }].slice(-MAX_TOASTS))
  }

  function upsertJob(job: IngestJob) {
    setJobs((prev) => {
      const idx = prev.findIndex((j) => j.id === job.id)
      if (idx === -1) return [job, ...prev].slice(0, 12)
      const next = [...prev]
      next[idx] = job
      return next
    })
  }

  async function trackJob(job: IngestJob) {
    upsertJob(job)
    const controller = new AbortController()
    pollControllers.current.set(job.id, controller)

    try {
      const finalJob = await waitForIngestJob(job.id, upsertJob, {
        signal: controller.signal,
      })

      if (finalJob.status === 'succeeded') {
        if (finalJob.chunks_added === 0) {
          pushToast({ tone: 'warn', text: `${finalJob.label} — 0 chunks. ${finalJob.message}` })
        } else {
          pushToast({
            tone: 'ok',
            text: `${finalJob.label} — ${finalJob.chunks_added} chunks`,
          })
        }
        await refresh()
        onChanged?.()
      } else if (finalJob.status === 'cancelled') {
        pushToast({ tone: 'warn', text: `Cancelled: ${finalJob.label}` })
      } else {
        pushToast({
          tone: 'err',
          text: `${finalJob.label}: ${finalJob.error || finalJob.message || 'failed'}`,
        })
      }
    } catch (err) {
      if (!(err instanceof ApiError && err.status === 499)) {
        pushToast({ tone: 'err', text: errText(err, 'Ingest failed') })
      }
    } finally {
      pollControllers.current.delete(job.id)
    }
  }

  async function handleFiles(files: FileList | File[]) {
    const list = Array.from(files)
    if (!list.length || busy) return

    setBusy(true)
    try {
      const started: IngestJob[] = []
      for (const file of list) {
        try {
          started.push(await startFileIngestJob(file))
        } catch (err) {
          pushToast({
            tone: 'err',
            text: `${file.name}: ${errText(err, 'failed to start ingest')}`,
          })
        }
      }
      // Track jobs concurrently (backend runs each on its own thread)
      await Promise.all(started.map((job) => trackJob(job)))
    } finally {
      setBusy(false)
    }
  }

  async function onUrlSubmit(e: FormEvent) {
    e.preventDefault()
    const u = url.trim()
    if (!u || busy) return

    setBusy(true)
    try {
      const job = await startUrlIngestJob(u)
      setUrl('')
      await trackJob(job)
    } catch (err) {
      pushToast({
        tone: 'err',
        text: errText(err, 'URL ingest failed'),
      })
    } finally {
      setBusy(false)
    }
  }

  async function onCancel(jobId: string) {
    setCancellingId(jobId)
    try {
      const job = await cancelIngestJob(jobId)
      upsertJob(job)
    } catch (err) {
      pushToast({ tone: 'err', text: errText(err, 'Cancel failed') })
    } finally {
      setCancellingId(null)
    }
  }

  async function onDelete(source: string) {
    try {
      await deleteSource(source)
      pushToast({ tone: 'ok', text: 'Source deleted' })
      await refresh()
      onChanged?.()
    } catch (err) {
      pushToast({
        tone: 'err',
        text: errText(err, 'Delete failed'),
      })
    }
  }

  function onDrop(e: DragEvent) {
    e.preventDefault()
    setDragOver(false)
    if (e.dataTransfer.files?.length) void handleFiles(e.dataTransfer.files)
  }

  const activeCount = jobs.filter(
    (j) => j.status === 'queued' || j.status === 'running',
  ).length

  return (
    <div className={styles.wrap}>
      <header className={styles.header}>
        <div>
          <h1>Library</h1>
          <p>Upload documents or ingest a URL. Scanned PDFs use OCR when available.</p>
        </div>
      </header>

      <div className={styles.grid}>
        <section
          className={`${styles.drop} ${dragOver ? styles.over : ''}`}
          onDragOver={(e) => {
            e.preventDefault()
            setDragOver(true)
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
        >
          <h2>Upload files</h2>
          <p>PDF, DOCX, CSV, JSON — drop here or browse</p>
          <label className={styles.fileBtn}>
            {busy ? 'Working…' : 'Choose files'}
            <input
              type="file"
              multiple
              accept={ACCEPT}
              disabled={busy}
              onChange={(e) => {
                if (e.target.files) void handleFiles(e.target.files)
                e.target.value = ''
              }}
            />
          </label>
          <p className={styles.hint}>For files over ~2 GB use: python scripts/ingest_cli.py</p>
        </section>

        <section className={styles.urlBox}>
          <h2>Ingest URL</h2>
          <p>Fetch and index any webpage</p>
          <form onSubmit={onUrlSubmit}>
            <label htmlFor="ingest-url" className="sr-only">Page URL</label>
            <input
              id="ingest-url"
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://example.com/article"
              disabled={busy}
            />
            <button type="submit" disabled={busy || !url.trim()}>
              {busy && activeCount ? 'Working…' : 'Ingest'}
            </button>
          </form>
        </section>
      </div>

      <IngestProgress
        jobs={jobs}
        onCancel={(id) => void onCancel(id)}
        cancellingId={cancellingId}
      />

      {toasts.length > 0 && (
        <div className={styles.toasts}>
          {toasts.map((t) => (
            <div key={t.id} className={styles[t.tone]}>
              {t.text}
            </div>
          ))}
        </div>
      )}

      <section className={styles.list}>
        <div className={styles.listHead}>
          <h2>Sources</h2>
          <button type="button" onClick={() => void refresh()} disabled={busy}>
            Refresh
          </button>
        </div>
        {error && <p className={styles.err}>{error}</p>}
        {!sources.length && !error && (
          <p className={styles.empty}>No sources yet. Upload a file or add a URL.</p>
        )}
        {sources.length > 0 && (
          <table className={styles.table}>
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Type</th>
                <th scope="col"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {sources.map((src) => (
                <tr key={src.source}>
                  <td>
                    <span className={styles.name} title={src.source}>{src.filename}</span>
                    <span className={styles.path}>{src.source}</span>
                  </td>
                  <td>
                    <span className={`${styles.badge} ${styles[src.type as keyof typeof styles] || ''}`}>
                      {src.type.toUpperCase()}
                    </span>
                  </td>
                  <td className={styles.actions}>
                    <button type="button" onClick={() => void onDelete(src.source)} disabled={busy}>
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}
