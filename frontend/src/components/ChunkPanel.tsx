import { useState } from 'react'
import type { RetrievedChunk } from '../api/client'
import styles from './ChunkPanel.module.css'

type Props = {
  chunks: RetrievedChunk[]
  warning?: string | null
  avgScore?: number | null
}

function scoreClass(score: number) {
  if (score >= 0.4) return styles.high
  if (score >= 0.25) return styles.mid
  return styles.low
}

export function ChunkPanel({ chunks, warning, avgScore }: Props) {
  const [open, setOpen] = useState(true)

  if (!chunks.length && !warning) return null

  return (
    <div className={styles.panel}>
      <button type="button" className={styles.toggle} onClick={() => setOpen((v) => !v)}>
        <span>Retrieved chunks ({chunks.length})</span>
        <span className={styles.chev}>{open ? '−' : '+'}</span>
      </button>
      {open && (
        <div className={styles.body}>
          {warning && <p className={styles.warn}>{warning}</p>}
          {typeof avgScore === 'number' && chunks.length > 0 && (
            <p className={styles.avg}>Avg relevance: {avgScore.toFixed(3)}</p>
          )}
          {chunks.map((chunk) => (
            <article key={`${chunk.index}-${chunk.source}`} className={styles.chunk}>
              <header>
                <strong>Source {chunk.index}</strong>
                <span>
                  {chunk.source} ({chunk.type}
                  {chunk.page != null ? `, page ${chunk.page}` : ''})
                </span>
                <em className={scoreClass(chunk.score)}>score {chunk.score.toFixed(3)}</em>
              </header>
              <pre>{chunk.content.slice(0, 1200)}{chunk.content.length > 1200 ? '…' : ''}</pre>
            </article>
          ))}
        </div>
      )}
    </div>
  )
}
