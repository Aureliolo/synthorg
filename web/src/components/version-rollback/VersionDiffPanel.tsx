/** Inner content for the version-diff Drawer: skeleton / empty / entry list. */

import { ErrorBanner } from '@/components/ui/error-banner'
import { Skeleton } from '@/components/ui/skeleton'
import type { VersionDiffResponse } from '@/api/endpoints/version-history'

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return '∅'
  if (typeof value === 'string') return value
  return JSON.stringify(value, null, 2)
}

function DiffSkeletonList() {
  return (
    <div className="flex flex-col gap-grid-gap">
      {[1, 2, 3].map((i) => (
        <Skeleton key={i} className="h-16 w-full" />
      ))}
    </div>
  )
}

function DiffEntryRow({ entry }: { entry: VersionDiffResponse['entries'][number] }) {
  return (
    <li className="rounded-md border border-border bg-card p-card">
      <p className="mb-1 font-mono text-xs text-text-secondary">{entry.path}</p>
      <div className="grid grid-cols-1 gap-grid-gap sm:grid-cols-2">
        <div>
          <p className="text-xs text-text-secondary">Before</p>
          <pre className="overflow-x-auto rounded-md bg-surface p-2 font-mono text-xs text-foreground">
            {formatValue(entry.before)}
          </pre>
        </div>
        <div>
          <p className="text-xs text-text-secondary">After</p>
          <pre className="overflow-x-auto rounded-md bg-surface p-2 font-mono text-xs text-foreground">
            {formatValue(entry.after)}
          </pre>
        </div>
      </div>
    </li>
  )
}

export interface VersionDiffPanelProps {
  diff: VersionDiffResponse | null
  error: string | null
  isLoading: boolean
}

export function VersionDiffPanel({ diff, error, isLoading }: VersionDiffPanelProps) {
  if (error) {
    return (
      <ErrorBanner
        severity="error"
        title="Could not load diff"
        description={error}
      />
    )
  }
  if (isLoading) return <DiffSkeletonList />
  if (diff === null) return null
  if (diff.entries.length === 0) {
    return (
      <p className="text-sm text-text-secondary">
        No field-level changes between these versions.
      </p>
    )
  }
  return (
    <ul className="flex flex-col gap-grid-gap">
      {diff.entries.map((entry) => (
        <DiffEntryRow key={entry.path} entry={entry} />
      ))}
    </ul>
  )
}
