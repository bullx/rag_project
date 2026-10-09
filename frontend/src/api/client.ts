const API_BASE = '/api'

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export type ChatMessage = {
  role: 'user' | 'assistant'
  content: string
}

export type RetrievedChunk = {
  index: number
  source: string
  type: string
  page?: number | null
  score: number
  content: string
}

export type RetrieveResult = {
  chunks: RetrievedChunk[]
  count: number
  avg_score?: number | null
  warning?: string | null
}

export type KbStats = {
  total_chunks: number
  total_sources: number
  embedding_model?: string
  embedding_device?: string
  cuda_available?: boolean
  torch_version?: string | null
  min_relevance_score?: number
}

export type SourceItem = {
  source: string
  filename: string
  type: string
}

export type IngestResult = {
  success: boolean
  source: string
  chunks_added: number
  message: string
}

export type IngestJob = {
  id: string
  label: string
  kind: string
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | string
  progress: number
  stage: string
  message: string
  source: string
  chunks_added: number
  error?: string | null
  created_at: number
  updated_at: number
  cancellable: boolean
}

async function readError(res: Response): Promise<string> {
  try {
    const data = await res.json()
    const detail = data.detail ?? data.message
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      return detail
        .map((d) => (typeof d === 'string' ? d : d.msg || JSON.stringify(d)))
        .join('; ')
    }
    if (detail && typeof detail === 'object') return JSON.stringify(detail)
    return res.statusText || 'Request failed'
  } catch {
    return res.statusText || 'Request failed'
  }
}

async function ensureOk(res: Response): Promise<void> {
  if (!res.ok) throw new ApiError(await readError(res), res.status)
}

export async function getHealth(): Promise<{ status: string; tesseract?: boolean }> {
  const res = await fetch(`${API_BASE}/health`)
  await ensureOk(res)
  return res.json()
}

export async function getStats(): Promise<KbStats> {
  const res = await fetch(`${API_BASE}/stats`)
  await ensureOk(res)
  return res.json()
}

export async function getSources(): Promise<SourceItem[]> {
  const res = await fetch(`${API_BASE}/sources`)
  await ensureOk(res)
  const data = await res.json()
  return data.sources ?? []
}

export async function deleteSource(source: string): Promise<void> {
  const res = await fetch(
    `${API_BASE}/sources?source=${encodeURIComponent(source)}`,
    { method: 'DELETE' },
  )
  await ensureOk(res)
}

export async function retrieve(
  question: string,
  top_k = 6,
): Promise<RetrieveResult> {
  const res = await fetch(`${API_BASE}/retrieve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, top_k }),
  })
  await ensureOk(res)
  return res.json()
}

export async function startFileIngestJob(file: File): Promise<IngestJob> {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch(`${API_BASE}/ingest/file/jobs`, {
    method: 'POST',
    body: form,
  })
  await ensureOk(res)
  return res.json()
}

export async function startUrlIngestJob(url: string): Promise<IngestJob> {
  const res = await fetch(`${API_BASE}/ingest/url/jobs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }),
  })
  await ensureOk(res)
  return res.json()
}

export async function getIngestJob(jobId: string): Promise<IngestJob> {
  const res = await fetch(`${API_BASE}/ingest/jobs/${jobId}`)
  await ensureOk(res)
  return res.json()
}

export async function cancelIngestJob(jobId: string): Promise<IngestJob> {
  const res = await fetch(`${API_BASE}/ingest/jobs/${jobId}/cancel`, {
    method: 'POST',
  })
  await ensureOk(res)
  return res.json()
}

const TERMINAL = new Set(['succeeded', 'failed', 'cancelled'])

export async function waitForIngestJob(
  jobId: string,
  onUpdate: (job: IngestJob) => void,
  opts?: { intervalMs?: number; signal?: AbortSignal },
): Promise<IngestJob> {
  const interval = opts?.intervalMs ?? 400
  let job = await getIngestJob(jobId)
  onUpdate(job)

  while (!TERMINAL.has(job.status)) {
    if (opts?.signal?.aborted) {
      throw new ApiError('Polling aborted', 499)
    }
    await new Promise((r) => setTimeout(r, interval))
    job = await getIngestJob(jobId)
    onUpdate(job)
  }
  return job
}

export class StreamAbortedError extends Error {
  constructor(message = 'Stopped') {
    super(message)
    this.name = 'StreamAbortedError'
  }
}

export async function streamPost(
  endpoint: string,
  body: unknown,
  onChunk: (text: string) => void,
  signal?: AbortSignal,
): Promise<string> {
  try {
    const res = await fetch(`${API_BASE}${endpoint}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    })
    await ensureOk(res)
    if (!res.body) throw new ApiError('No response body', res.status)

    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let full = ''

    while (true) {
      if (signal?.aborted) {
        await reader.cancel()
        throw new StreamAbortedError()
      }
      const { done, value } = await reader.read()
      if (done) break
      const text = decoder.decode(value, { stream: true })
      full += text
      onChunk(text)
    }

    return full
  } catch (err) {
    if (
      (err instanceof DOMException && err.name === 'AbortError') ||
      (err instanceof Error && err.name === 'AbortError')
    ) {
      throw new StreamAbortedError()
    }
    throw err
  }
}

export function askStream(
  payload: {
    question: string
    chat_history: ChatMessage[]
    top_k: number
    response_mode: 'answer' | 'list'
  },
  onChunk: (text: string) => void,
  signal?: AbortSignal,
) {
  return streamPost('/ask/stream', payload, onChunk, signal)
}

export function researchStream(
  payload: { topic: string; top_k: number },
  onChunk: (text: string) => void,
  signal?: AbortSignal,
) {
  return streamPost('/research', payload, onChunk, signal)
}
