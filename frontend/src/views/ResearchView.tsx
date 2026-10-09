import { useEffect, useRef, useState, type FormEvent } from 'react'
import { motion } from 'framer-motion'
import { researchStream, StreamAbortedError } from '../api/client'
import { Markdown } from '../components/Markdown'
import styles from './ResearchView.module.css'

export function ResearchView() {
  const [topic, setTopic] = useState('')
  const [topK, setTopK] = useState(10)
  const [report, setReport] = useState('')
  const [busy, setBusy] = useState(false)
  const [stopped, setStopped] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [activeTopic, setActiveTopic] = useState('')
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    return () => {
      abortRef.current?.abort()
    }
  }, [])

  function stop() {
    abortRef.current?.abort()
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    const t = topic.trim()
    if (!t || busy) return

    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    setBusy(true)
    setStopped(false)
    setError(null)
    setReport('')
    setActiveTopic(t)

    try {
      await researchStream(
        { topic: t, top_k: topK },
        (delta) => {
          setReport((r) => r + delta)
        },
        controller.signal,
      )
    } catch (err) {
      if (err instanceof StreamAbortedError) {
        setStopped(true)
        setReport((r) => (r ? `${r}\n\n_(stopped)_` : '_(stopped)_'))
      } else {
        setError(err instanceof Error ? err.message : 'Research failed')
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null
      setBusy(false)
    }
  }

  function download() {
    const blob = new Blob([report], { type: 'text/markdown' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `research_${activeTopic.slice(0, 30).replace(/\s+/g, '_')}.md`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className={styles.wrap}>
      <header className={styles.header}>
        <div>
          <h1>Research</h1>
          <p>Generate a structured deep-dive: overview, findings, analysis, gaps.</p>
        </div>
      </header>

      <form className={styles.form} onSubmit={onSubmit}>
        <label htmlFor="research-topic" className="sr-only">Topic</label>
        <input
          id="research-topic"
          type="text"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          placeholder="e.g. climate policy, Q3 results, neural networks"
          disabled={busy}
        />
        <label className={styles.slider} htmlFor="research-chunks">
          <span>Chunks {topK}</span>
          <input
            id="research-chunks"
            type="range"
            min={4}
            max={20}
            value={topK}
            onChange={(e) => setTopK(Number(e.target.value))}
            disabled={busy}
          />
        </label>
        {busy ? (
          <button type="button" className={styles.stop} onClick={stop}>
            Stop
          </button>
        ) : (
          <button type="submit" disabled={!topic.trim()}>
            Generate
          </button>
        )}
      </form>

      {error && <p className={styles.error}>{error}</p>}
      {stopped && !busy && <p className={styles.stoppedNote}>Generation stopped.</p>}

      {!report && !busy && (
        <motion.div
          className={styles.empty}
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
        >
          <h2>Pick a broad topic</h2>
          <p>Best for synthesis across sources — not for listing every row.</p>
        </motion.div>
      )}

      {(report || busy) && (
        <motion.article
          className={styles.report}
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
        >
          <div className={styles.reportHead}>
            <h2>Research: {activeTopic}</h2>
            <div className={styles.reportActions}>
              {busy && (
                <button type="button" className={styles.stop} onClick={stop}>
                  Stop
                </button>
              )}
              {report && !busy && (
                <button type="button" onClick={download}>
                  Download .md
                </button>
              )}
            </div>
          </div>
          {report ? (
            <Markdown content={report} streaming={busy} />
          ) : (
            <p className={styles.thinking}>Gathering sources…</p>
          )}
        </motion.article>
      )}
    </div>
  )
}
