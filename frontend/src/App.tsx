import { useCallback, useEffect, useState } from 'react'
import { MotionConfig } from 'framer-motion'
import { getHealth, getStats, type KbStats } from './api/client'
import { ChatView } from './views/ChatView'
import { ResearchView } from './views/ResearchView'
import { KnowledgeView } from './views/KnowledgeView'
import styles from './App.module.css'

type View = 'chat' | 'research' | 'library'

function NavIcon({ id }: { id: View }) {
  const props = {
    width: 18,
    height: 18,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.75,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    'aria-hidden': true,
  }
  if (id === 'chat') {
    return (
      <svg {...props}>
        <path d="M8 9h8M8 13h5" />
        <path d="M6 5h12a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H10l-4 3v-3H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z" />
      </svg>
    )
  }
  if (id === 'research') {
    return (
      <svg {...props}>
        <circle cx="11" cy="11" r="6" />
        <path d="M16 16l4 4" />
      </svg>
    )
  }
  return (
    <svg {...props}>
      <path d="M4 7h6v12H4zM14 7h6v5h-6zM14 15h6v4h-6z" />
    </svg>
  )
}

const NAV: { id: View; label: string; hint: string }[] = [
  { id: 'chat', label: 'Chat', hint: 'Q&A with citations' },
  { id: 'research', label: 'Research', hint: 'Structured deep-dives' },
  { id: 'library', label: 'Library', hint: 'Sources & ingest' },
]

export default function App() {
  const [view, setView] = useState<View>('chat')
  const [stats, setStats] = useState<KbStats>({ total_chunks: 0, total_sources: 0 })
  const [apiOk, setApiOk] = useState<boolean | null>(null)

  const refreshStats = useCallback(async () => {
    try {
      const [s, h] = await Promise.all([getStats(), getHealth()])
      setStats(s)
      setApiOk(h.status === 'ok')
    } catch {
      setApiOk(false)
    }
  }, [])

  useEffect(() => {
    void refreshStats()
    const id = window.setInterval(() => void refreshStats(), 15000)
    return () => window.clearInterval(id)
  }, [refreshStats])

  return (
    <MotionConfig reducedMotion="user">
    <a className={styles.skip} href="#main">Skip to content</a>
    <div className={styles.shell}>
      <aside className={styles.sidebar}>
        <div className={styles.brand}>
          <span className={styles.logo} aria-hidden="true">R</span>
          <div>
            <strong>RAG</strong>
            <p>Knowledge assistant</p>
          </div>
        </div>

        <p className={styles.navLabel}>Workspace</p>

        <nav className={styles.nav}>
          {NAV.map((item) => (
            <button
              key={item.id}
              type="button"
              className={view === item.id ? styles.active : ''}
              onClick={() => setView(item.id)}
              aria-current={view === item.id ? 'page' : undefined}
            >
              <NavIcon id={item.id} />
              <span>{item.label}</span>
              <small>{item.hint}</small>
            </button>
          ))}
        </nav>

        <div className={styles.stats}>
          <div>
            <strong>{stats.total_chunks.toLocaleString()}</strong>
            <span>chunks indexed</span>
          </div>
          <div>
            <strong>{stats.total_sources.toLocaleString()}</strong>
            <span>sources</span>
          </div>
        </div>

        <footer className={styles.meta}>
          <div className={`${styles.dot} ${apiOk ? styles.on : apiOk === false ? styles.off : ''}`} />
          <div>
            <div>API {apiOk ? 'online' : apiOk === false ? 'offline' : '…'}</div>
            <div className={styles.caption}>
              {stats.embedding_model || 'BAAI/bge-small-en-v1.5'}
              {stats.embedding_device ? ` · ${stats.embedding_device}` : ''}
              {stats.min_relevance_score != null
                ? ` · min ${stats.min_relevance_score}`
                : ''}
              {stats.torch_version ? ` · torch ${stats.torch_version}` : ''}
            </div>
          </div>
        </footer>
      </aside>

      <main id="main" className={styles.main}>
        <div className={styles.panel} hidden={view !== 'chat'} inert={view !== 'chat'}>
          <ChatView />
        </div>
        <div className={styles.panel} hidden={view !== 'research'} inert={view !== 'research'}>
          <ResearchView />
        </div>
        <div className={styles.panel} hidden={view !== 'library'} inert={view !== 'library'}>
          <KnowledgeView onChanged={refreshStats} />
        </div>
      </main>
    </div>
    </MotionConfig>
  )
}
