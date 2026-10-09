import type { IngestJob } from '../api/client'
import styles from './IngestProgress.module.css'

type Props = {
  jobs: IngestJob[]
  onCancel: (jobId: string) => void
  cancellingId?: string | null
}

function tone(status: string) {
  if (status === 'succeeded') return styles.ok
  if (status === 'failed') return styles.err
  if (status === 'cancelled') return styles.warn
  return styles.active
}

export function IngestProgress({ jobs, onCancel, cancellingId }: Props) {
  if (!jobs.length) return null

  return (
    <div className={styles.wrap} aria-live="polite">
      <div className={styles.head}>
        <h3>Ingest progress</h3>
        <span>{jobs.filter((j) => j.status === 'running' || j.status === 'queued').length} active</span>
      </div>
      <ul className={styles.list}>
        {jobs.map((job) => (
          <li key={job.id} className={`${styles.item} ${tone(job.status)}`}>
            <div className={styles.row}>
              <strong title={job.label}>{job.label}</strong>
              <em>{Math.round(job.progress)}%</em>
            </div>
            <div className={styles.barTrack}>
              <div
                className={styles.barFill}
                style={{ width: `${Math.max(2, Math.min(100, job.progress))}%` }}
              />
            </div>
            <div className={styles.meta}>
              <span>{job.message || job.stage}</span>
              {job.cancellable && (
                <button
                  type="button"
                  onClick={() => onCancel(job.id)}
                  disabled={cancellingId === job.id}
                >
                  {cancellingId === job.id ? 'Cancelling…' : 'Cancel'}
                </button>
              )}
            </div>
            {job.error && <p className={styles.error}>{job.error}</p>}
          </li>
        ))}
      </ul>
    </div>
  )
}
