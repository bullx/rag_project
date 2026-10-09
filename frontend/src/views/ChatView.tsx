import { useEffect, useRef, useState, type FormEvent } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  askStream,
  retrieve,
  StreamAbortedError,
  type ChatMessage,
  type RetrievedChunk,
} from '../api/client'
import { ChunkPanel } from '../components/ChunkPanel'
import { Markdown } from '../components/Markdown'
import styles from './ChatView.module.css'

type Turn = {
  id: string
  question: string
  answer: string
  chunks: RetrievedChunk[]
  warning?: string | null
  avgScore?: number | null
  streaming?: boolean
  stopped?: boolean
}

export function ChatView() {
  const [history, setHistory] = useState<ChatMessage[]>([])
  const [turns, setTurns] = useState<Turn[]>([])
  const [question, setQuestion] = useState('')
  const [mode, setMode] = useState<'answer' | 'list'>('answer')
  const [topK, setTopK] = useState(6)
  const [debugMode, setDebugMode] = useState(() => {
    try {
      return localStorage.getItem('rag.debugChunks') === '1'
    } catch {
      return false
    }
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    return () => {
      abortRef.current?.abort()
    }
  }, [])

  function toggleDebug() {
    setDebugMode((prev) => {
      const next = !prev
      try {
        localStorage.setItem('rag.debugChunks', next ? '1' : '0')
      } catch {
        /* ignore */
      }
      return next
    })
  }

  function stop() {
    abortRef.current?.abort()
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    const q = question.trim()
    if (!q || busy) return

    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    setBusy(true)
    setError(null)
    setQuestion('')
    const id = crypto.randomUUID()
    const prior = history

    setTurns((t) => [
      ...t,
      { id, question: q, answer: '', chunks: [], streaming: true },
    ])

    try {
      // Debug only: preview retrieved chunks (extra /retrieve call).
      if (debugMode) {
        const retrieved = await retrieve(q, topK)
        if (controller.signal.aborted) throw new StreamAbortedError()
        setTurns((t) =>
          t.map((turn) =>
            turn.id === id
              ? {
                  ...turn,
                  chunks: retrieved.chunks ?? [],
                  warning: retrieved.warning,
                  avgScore: retrieved.avg_score,
                }
              : turn,
          ),
        )
      }

      const answer = await askStream(
        {
          question: q,
          chat_history: prior,
          top_k: topK,
          response_mode: mode,
        },
        (delta) => {
          setTurns((t) =>
            t.map((turn) =>
              turn.id === id ? { ...turn, answer: turn.answer + delta } : turn,
            ),
          )
          bottomRef.current?.scrollIntoView({
            behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
          })
        },
        controller.signal,
      )

      setHistory((h) => [
        ...h,
        { role: 'user', content: q },
        { role: 'assistant', content: answer },
      ])
      setTurns((t) =>
        t.map((turn) => (turn.id === id ? { ...turn, streaming: false } : turn)),
      )
    } catch (err) {
      if (err instanceof StreamAbortedError) {
        let partial = ''
        setTurns((t) =>
          t.map((turn) => {
            if (turn.id !== id) return turn
            partial = turn.answer
            const answer = partial ? `${partial}\n\n_(stopped)_` : '_(stopped)_'
            return { ...turn, answer, streaming: false, stopped: true }
          }),
        )
        if (partial) {
          setHistory((h) => [
            ...h,
            { role: 'user', content: q },
            { role: 'assistant', content: `${partial}\n\n_(stopped)_` },
          ])
        }
      } else {
        const msg = err instanceof Error ? err.message : 'Chat failed'
        setError(msg)
        setTurns((t) =>
          t.map((turn) =>
            turn.id === id
              ? { ...turn, answer: turn.answer || `Error: ${msg}`, streaming: false }
              : turn,
          ),
        )
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null
      setBusy(false)
      bottomRef.current?.scrollIntoView({
        behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
      })
    }
  }

  function clearChat() {
    abortRef.current?.abort()
    setHistory([])
    setTurns([])
    setError(null)
  }

  return (
    <div className={styles.wrap}>
      <header className={styles.header}>
        <div>
          <h1>Chat</h1>
          <p>Ask a specific question. Use List mode for every matching item.</p>
        </div>
        <div className={styles.controls}>
          <div className={styles.segment}>
            <button
              type="button"
              className={mode === 'answer' ? styles.active : ''}
              onClick={() => {
                setMode('answer')
                setTopK(6)
              }}
            >
              Answer
            </button>
            <button
              type="button"
              className={mode === 'list' ? styles.active : ''}
              onClick={() => {
                setMode('list')
                setTopK(20)
              }}
            >
              List matches
            </button>
          </div>
          <label className={styles.slider} htmlFor="chat-chunks">
            <span>Chunks {topK}</span>
            <input
              id="chat-chunks"
              type="range"
              min={6}
              max={50}
              value={topK}
              onChange={(e) => setTopK(Number(e.target.value))}
              disabled={busy}
            />
          </label>
          <button
            type="button"
            className={`${styles.ghost} ${debugMode ? styles.debugOn : ''}`}
            onClick={toggleDebug}
            title="Show retrieved chunks (debug)"
          >
            {debugMode ? 'Debug on' : 'Debug'}
          </button>
          <button type="button" className={styles.ghost} onClick={clearChat} disabled={!turns.length && !busy}>
            Clear
          </button>
        </div>
      </header>

      <div className={styles.stage}>
      <div className={styles.thread}>
        {turns.length === 0 && (
          <motion.div
            className={styles.empty}
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.45 }}
          >
            <h2>Ask your knowledge base</h2>
            <p>Try “What are the key findings?” or switch to List mode for exhaustive matches.</p>
          </motion.div>
        )}

        <AnimatePresence initial={false}>
          {turns.map((turn) => (
            <motion.section
              key={turn.id}
              className={styles.turn}
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.35 }}
            >
              <div className={styles.user}>{turn.question}</div>
              {debugMode && (
                <ChunkPanel
                  chunks={turn.chunks}
                  warning={turn.warning}
                  avgScore={turn.avgScore}
                />
              )}
              <div className={styles.assistant}>
                {turn.answer ? (
                  <Markdown content={turn.answer} streaming={turn.streaming} />
                ) : (
                  <span className={styles.thinking}>Thinking…</span>
                )}
                {turn.stopped && <p className={styles.stopped}>Stopped</p>}
              </div>
            </motion.section>
          ))}
        </AnimatePresence>
        <div ref={bottomRef} />
      </div>

      {error && <p className={styles.error}>{error}</p>}

      <form className={styles.composer} onSubmit={onSubmit}>
        <label htmlFor="chat-question" className="sr-only">Question</label>
        <input
          id="chat-question"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="What does the knowledge base say about…"
          disabled={busy}
        />
        {busy ? (
          <button type="button" className={styles.stop} onClick={stop}>
            Stop
          </button>
        ) : (
          <button type="submit" disabled={!question.trim()}>
            Send
          </button>
        )}
        </form>
      </div>
    </div>
  )
}
