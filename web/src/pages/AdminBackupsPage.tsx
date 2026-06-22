/**
 * Admin backup inventory.
 *
 * Surfaces the full backup lifecycle (GET /admin/backups, POST
 * /admin/backups, POST /admin/backups/restore, DELETE
 * /admin/backups/{id}) for operators. Restore is a heavyweight
 * operation: the backend mints a safety backup first and a restart may
 * be required, so it is guarded by a destructive confirm that spells
 * that out.
 */
import { useEffect, useMemo, useState } from 'react'
import { ChevronDown, ChevronUp, HardDrive, Loader2, Plus, RotateCcw, Trash2 } from 'lucide-react'
import { useBackupsStore } from '@/stores/backups'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { SearchFilterSort } from '@/components/ui/search-filter-sort'
import { SearchInput } from '@/components/ui/search-input'
import { SectionCard } from '@/components/ui/section-card'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { StatPill } from '@/components/ui/stat-pill'
import { formatDateTime, formatFileSize, formatLabel } from '@/utils/format'
import type { BackupInfo } from '@/api/types/backup'

type PendingAction = { kind: 'delete' | 'restore'; backupId: string }

type BackupSortKey = 'timestamp' | 'size_bytes'

const BACKUP_SORT_VALUE: Record<BackupSortKey, (b: BackupInfo) => number> = {
  timestamp: (b) => new Date(b.timestamp).getTime(),
  size_bytes: (b) => b.size_bytes,
}

interface BackupSort {
  key: BackupSortKey
  dir: 'asc' | 'desc'
}

function BackupSortHeader({
  label,
  sortKey,
  sort,
  onSort,
  className,
}: {
  label: string
  sortKey: BackupSortKey
  sort: BackupSort
  onSort: (key: BackupSortKey) => void
  className?: string
}) {
  const isActive = sort.key === sortKey
  return (
    <th className={className}>
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className="inline-flex items-center gap-1 font-medium hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded"
        aria-label={`Sort by ${label}`}
      >
        {label}
        {isActive &&
          (sort.dir === 'asc' ? (
            <ChevronUp className="size-3" aria-hidden="true" />
          ) : (
            <ChevronDown className="size-3" aria-hidden="true" />
          ))}
      </button>
    </th>
  )
}

interface DialogCopy {
  title: string
  description: string
  confirmLabel: string
}

function dialogCopy(pending: PendingAction): DialogCopy {
  if (pending.kind === 'delete') {
    return {
      title: 'Delete backup',
      description:
        'Permanently delete this backup? Its archive is removed and this cannot be undone.',
      confirmLabel: 'Delete',
    }
  }
  return {
    title: 'Restore backup',
    description:
      'Restore the system from this backup? A safety backup is created first, and a restart may be required to apply the restored data.',
    confirmLabel: 'Restore',
  }
}

interface BackupsView {
  sortedBackups: readonly BackupInfo[]
  sort: BackupSort
  handleSort: (key: BackupSortKey) => void
  search: string
  setSearch: (value: string) => void
  triggerFilter: string
  setTriggerFilter: (value: string) => void
  triggerOptions: ReadonlyArray<{ value: string; label: string }>
}

/** Owns the search / trigger-filter / sort state and derives the visible rows. */
function useBackupsView(backups: readonly BackupInfo[]): BackupsView {
  const [sort, setSort] = useState<BackupSort>({ key: 'timestamp', dir: 'desc' })
  const [search, setSearch] = useState('')
  const [triggerFilter, setTriggerFilter] = useState('all')

  const handleSort = (key: BackupSortKey) => {
    setSort((prev) =>
      prev.key === key
        ? { key, dir: prev.dir === 'asc' ? 'desc' : 'asc' }
        : { key, dir: 'desc' },
    )
  }

  const triggerOptions = useMemo(() => {
    const present = new Set(backups.map((b) => b.trigger))
    return [
      { value: 'all', label: 'All triggers' },
      ...[...present].sort().map((t) => ({ value: t, label: formatLabel(t) })),
    ]
  }, [backups])

  const sortedBackups = useMemo(() => {
    const query = search.trim().toLowerCase()
    const filtered = backups.filter(
      (b) =>
        (triggerFilter === 'all' || b.trigger === triggerFilter)
        && (query === '' || b.backup_id.toLowerCase().includes(query)),
    )
    const valueOf = BACKUP_SORT_VALUE[sort.key]
    const factor = sort.dir === 'asc' ? 1 : -1
    return [...filtered].sort((a, b) => (valueOf(a) - valueOf(b)) * factor)
  }, [backups, sort, search, triggerFilter])

  return {
    sortedBackups,
    sort,
    handleSort,
    search,
    setSearch,
    triggerFilter,
    setTriggerFilter,
    triggerOptions,
  }
}

function BackupsToolbar({ view }: { view: BackupsView }) {
  return (
    <SearchFilterSort
      search={
        <SearchInput
          value={view.search}
          onChange={view.setSearch}
          placeholder="Search by backup ID..."
          ariaLabel="Search backups"
        />
      }
      filters={
        <SegmentedControl
          label="Filter by trigger"
          value={view.triggerFilter}
          onChange={view.setTriggerFilter}
          options={view.triggerOptions}
          size="sm"
        />
      }
    />
  )
}

export default function AdminBackupsPage() {
  const backups = useBackupsStore((s) => s.backups)
  const loading = useBackupsStore((s) => s.loading)
  const loadingMore = useBackupsStore((s) => s.loadingMore)
  const hasMore = useBackupsStore((s) => s.hasMore)
  const error = useBackupsStore((s) => s.error)
  const mutating = useBackupsStore((s) => s.mutating)
  const fetchBackups = useBackupsStore((s) => s.fetchBackups)
  const fetchMoreBackups = useBackupsStore((s) => s.fetchMoreBackups)
  const createBackup = useBackupsStore((s) => s.createBackup)
  const deleteBackup = useBackupsStore((s) => s.deleteBackup)
  const restoreBackup = useBackupsStore((s) => s.restoreBackup)
  const [pending, setPending] = useState<PendingAction | null>(null)
  const view = useBackupsView(backups)
  const { sortedBackups, sort, handleSort } = view

  useEffect(() => {
    void fetchBackups()
  }, [fetchBackups])

  return (
    <div className="space-y-section-gap">
      <ListHeader
        title="Backups"
        description="Create, restore, and delete system backups."
        count={backups.length}
        primaryAction={
          <Button size="sm" onClick={() => void createBackup()} disabled={mutating}>
            <Plus aria-hidden="true" />
            Create backup
          </Button>
        }
      />

      {error && (
        <ErrorBanner severity="error" title="Could not load backups" description={error} />
      )}

      <BackupsToolbar view={view} />

      <BackupsBody
        backups={sortedBackups}
        loading={loading}
        loadingMore={loadingMore}
        hasMore={hasMore}
        error={error}
        sort={sort}
        onSort={handleSort}
        onDelete={(id) => setPending({ kind: 'delete', backupId: id })}
        onRestore={(id) => setPending({ kind: 'restore', backupId: id })}
        onLoadMore={() => void fetchMoreBackups()}
      />

      {pending && (
        <ConfirmDialog
          open
          onOpenChange={(open) => {
            if (!open) setPending(null)
          }}
          variant="destructive"
          loading={mutating}
          {...dialogCopy(pending)}
          onConfirm={async () => {
            const ok =
              pending.kind === 'delete'
                ? await deleteBackup(pending.backupId)
                : await restoreBackup(pending.backupId)
            if (ok) setPending(null)
            return ok
          }}
        />
      )}
    </div>
  )
}

interface BackupsBodyProps {
  backups: readonly BackupInfo[]
  loading: boolean
  loadingMore: boolean
  hasMore: boolean
  error: string | null
  sort: BackupSort
  onSort: (key: BackupSortKey) => void
  onDelete: (id: string) => void
  onRestore: (id: string) => void
  onLoadMore: () => void
}

function BackupsBody({
  backups,
  loading,
  loadingMore,
  hasMore,
  error,
  sort,
  onSort,
  onDelete,
  onRestore,
  onLoadMore,
}: BackupsBodyProps) {
  if (loading && backups.length === 0) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="size-6 animate-spin text-text-muted" />
      </div>
    )
  }
  if (backups.length === 0) {
    if (error !== null) return null
    return (
      <EmptyState
        icon={HardDrive}
        title="No backups yet"
        description="Create a backup to capture the current system state."
      />
    )
  }
  return (
    <SectionCard title="Backups" icon={HardDrive}>
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full text-xs">
          <thead className="bg-surface text-left text-text-secondary">
            <tr>
              <th className="px-3 py-2 font-medium">Backup</th>
              <BackupSortHeader
                label="Created"
                sortKey="timestamp"
                sort={sort}
                onSort={onSort}
                className="w-44 px-3 py-2 font-medium"
              />
              <th className="w-28 px-3 py-2 font-medium">Trigger</th>
              <BackupSortHeader
                label="Size"
                sortKey="size_bytes"
                sort={sort}
                onSort={onSort}
                className="w-24 px-3 py-2 font-medium"
              />
              <th className="px-3 py-2 font-medium">Components</th>
              <th className="w-44 px-3 py-2" />
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {backups.map((backup) => (
              <BackupRow
                key={backup.backup_id}
                backup={backup}
                onDelete={onDelete}
                onRestore={onRestore}
              />
            ))}
          </tbody>
        </table>
      </div>
      {hasMore && (
        <div className="mt-3 flex justify-center">
          <Button variant="outline" size="sm" onClick={onLoadMore} disabled={loadingMore}>
            {loadingMore ? 'Loading...' : 'Load more'}
          </Button>
        </div>
      )}
    </SectionCard>
  )
}

interface BackupRowProps {
  backup: BackupInfo
  onDelete: (id: string) => void
  onRestore: (id: string) => void
}

function BackupRow({ backup, onDelete, onRestore }: BackupRowProps) {
  return (
    <tr className="align-top">
      <td className="px-3 py-2 font-mono text-micro text-text-muted">{backup.backup_id}</td>
      <td className="px-3 py-2 text-text-secondary">{formatDateTime(backup.timestamp)}</td>
      <td className="px-3 py-2">
        <StatPill value={backup.trigger} />
      </td>
      <td className="px-3 py-2 text-text-secondary">{formatFileSize(backup.size_bytes)}</td>
      <td className="px-3 py-2 text-text-secondary">{backup.components.join(', ')}</td>
      <td className="px-3 py-2 text-right">
        <div className="flex justify-end gap-1">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onRestore(backup.backup_id)}
            aria-label={`Restore backup ${backup.backup_id}`}
          >
            <RotateCcw className="size-3.5" />
            Restore
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="text-danger hover:text-danger"
            onClick={() => onDelete(backup.backup_id)}
            aria-label={`Delete backup ${backup.backup_id}`}
          >
            <Trash2 className="size-3.5" />
            Delete
          </Button>
        </div>
      </td>
    </tr>
  )
}
