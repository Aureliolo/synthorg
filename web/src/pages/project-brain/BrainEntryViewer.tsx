import type { BrainEntry, BrainEntryVersion } from '@/api/types'
import { formatRelativeTime } from '@/utils/format'
import { BRAIN_KIND_LABEL, BRAIN_STATUS_LABEL } from './labels'

const EMPTY_VALUE = '-'

export interface BrainEntryViewerProps {
  entry: BrainEntry | null
  loading: boolean
  error: string | null
  versions: readonly BrainEntryVersion[] | null
  historyError: string | null
  onShowHistory: () => void
}

export function BrainEntryViewer({
  entry,
  loading,
  error,
  versions,
  historyError,
  onShowHistory,
}: BrainEntryViewerProps) {
  if (loading) {
    return <p className="text-muted-foreground text-sm">Loading entry...</p>
  }
  if (error !== null) {
    return <p className="text-destructive text-sm">{error}</p>
  }
  if (entry === null) {
    return (
      <p className="text-muted-foreground text-sm">
        Select an entry to see its rationale, payload, and revision history.
      </p>
    )
  }
  return (
    <article className="flex flex-col gap-4">
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <Pill text={BRAIN_KIND_LABEL[entry.entry_kind]} />
          <Pill text={BRAIN_STATUS_LABEL[entry.status]} />
          <span className="text-muted-foreground text-xs">r{entry.revision}</span>
        </div>
        <h1 className="text-lg font-semibold">{entry.title}</h1>
      </header>
      <p className="text-foreground/90 text-sm whitespace-pre-wrap">
        {entry.rationale}
      </p>
      <PayloadFields payload={entry.payload} />
      <MetaList entry={entry} />
      <HistoryPanel
        versions={versions}
        historyError={historyError}
        onShowHistory={onShowHistory}
      />
    </article>
  )
}

interface PayloadFieldsProps {
  payload: BrainEntry['payload']
}

function renderValue(value: unknown): string {
  if (value === null || value === undefined) return EMPTY_VALUE
  if (Array.isArray(value)) return value.length > 0 ? value.join(', ') : EMPTY_VALUE
  return String(value)
}

function PayloadFields({ payload }: PayloadFieldsProps) {
  const fields = Object.entries(payload).filter(([key]) => key !== 'entry_kind')
  if (fields.length === 0) return null
  return (
    <dl className="border-border grid grid-cols-[140px_1fr] gap-x-4 gap-y-1 border-t pt-3 text-sm">
      {fields.map(([key, value]) => (
        <DefRow key={key} term={key.replaceAll('_', ' ')} value={renderValue(value)} />
      ))}
    </dl>
  )
}

interface MetaListProps {
  entry: BrainEntry
}

function MetaList({ entry }: MetaListProps) {
  return (
    <dl className="border-border grid grid-cols-[140px_1fr] gap-x-4 gap-y-1 border-t pt-3 text-sm">
      <DefRow term="author" value={entry.author} />
      <DefRow term="recorded" value={formatRelativeTime(entry.recorded_at)} />
      {entry.confidence !== null && (
        <DefRow term="confidence" value={String(entry.confidence)} />
      )}
      {entry.tags.length > 0 && <DefRow term="tags" value={entry.tags.join(', ')} />}
      {entry.related_task_ids.length > 0 && (
        <DefRow term="tasks" value={entry.related_task_ids.join(', ')} />
      )}
    </dl>
  )
}

interface DefRowProps {
  term: string
  value: string
}

function DefRow({ term, value }: DefRowProps) {
  return (
    <>
      <dt className="text-muted-foreground">{term}</dt>
      <dd className="text-foreground/90 break-words">{value}</dd>
    </>
  )
}

interface HistoryPanelProps {
  versions: readonly BrainEntryVersion[] | null
  historyError: string | null
  onShowHistory: () => void
}

function HistoryPanel({ versions, historyError, onShowHistory }: HistoryPanelProps) {
  if (versions === null && historyError === null) {
    return (
      <button
        type="button"
        onClick={onShowHistory}
        className="border-border text-foreground/80 hover:bg-muted/50 self-start rounded border px-3 py-1 text-xs"
      >
        Show revision history
      </button>
    )
  }
  return (
    <section className="border-border flex flex-col gap-1 border-t pt-3">
      <h2 className="text-muted-foreground text-xs font-semibold uppercase">
        Revision history
      </h2>
      <HistoryContent versions={versions} historyError={historyError} />
    </section>
  )
}

interface HistoryContentProps {
  versions: readonly BrainEntryVersion[] | null
  historyError: string | null
}

function HistoryContent({ versions, historyError }: HistoryContentProps) {
  if (historyError !== null) {
    return <p className="text-destructive text-sm">{historyError}</p>
  }
  if (versions === null || versions.length === 0) {
    return (
      <p className="text-muted-foreground text-sm">
        No committed snapshots for this entry yet.
      </p>
    )
  }
  return (
    <ul className="flex flex-col gap-1 text-xs">
      {versions.map((version) => (
        <HistoryRow key={version.commit_hash} version={version} />
      ))}
    </ul>
  )
}

function HistoryRow({ version }: { version: BrainEntryVersion }) {
  return (
    <li className="text-foreground/80 flex flex-col">
      <span>
        {`r${version.revision} · ${version.commit_hash.slice(0, 8)} · `}
        {formatRelativeTime(version.committed_at)}
      </span>
      <span className="text-muted-foreground break-words">
        {version.summary}
        {` · ${version.author}`}
      </span>
    </li>
  )
}

interface PillProps {
  text: string
}

function Pill({ text }: PillProps) {
  return (
    <span className="border-border bg-muted/40 text-foreground/80 rounded-full border px-2 py-0.5 text-xs">
      {text}
    </span>
  )
}
