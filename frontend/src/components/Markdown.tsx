import ReactMarkdown from 'react-markdown'
import styles from './Markdown.module.css'

type Props = {
  content: string
  streaming?: boolean
}

export function Markdown({ content, streaming }: Props) {
  return (
    <div className={styles.md}>
      <ReactMarkdown>{content}</ReactMarkdown>
      {streaming && <span className={styles.caret} aria-hidden />}
    </div>
  )
}
