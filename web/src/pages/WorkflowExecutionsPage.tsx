/**
 * Workflow executions list.
 *
 * Lists recent runs for a single workflow definition with a Cancel action for
 * executions still in flight.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { ArrowDownWideNarrow, ArrowUpWideNarrow } from 'lucide-react'
import { useParams } from 'react-router'
import { Breadcrumbs } from '@/components/ui/breadcrumbs'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { Pagination } from '@/components/ui/pagination'
import { ProgressIndicator } from '@/components/ui/progress-indicator'
import { SearchFilterSort } from '@/components/ui/search-filter-sort'
import { SectionCard } from '@/components/ui/section-card'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { StatusBadge } from '@/components/ui/status-badge'
import { useListPagination } from '@/hooks/use-list-pagination'
import type { AgentRuntimeStatus } from '@/lib/utils'
import { ROUTES } from '@/router/routes'
import { formatDateTime } from '@/utils/format'
import type { WorkflowExecution } from '@/api/endpoints/workflow-executions'

import { useWorkflowExecutionsController } from './workflows/useWorkflowExecutionsController'

type ExecStatusFilter = 'all' | 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
type ExecSortDir = 'asc' | 'desc'

const STATUS_FILTER_OPTIONS: ReadonlyArray<{ value: ExecStatusFilter; label: string }> = [
  { value: 'all', label: 'All' },
  { value: 'pending', label: 'Pending' },
  { value: 'running', label: 'Running' },
  { value: 'completed', label: 'Completed' },
  { value: 'failed', label: 'Failed' },
  { value: 'cancelled', label: 'Cancelled' },
]

interface ExecutionsView {
  statusFilter: ExecStatusFilter
  setStatusFilter: (value: ExecStatusFilter) => void
  sortDir: ExecSortDir
  toggleSort: () => void
  processed: readonly WorkflowExecution[]
  page: number
  pageSize: number
  totalItems: number
  paginatedItems: readonly WorkflowExecution[]
  setPage: (page: number) => void
  setPageSize: (size: number) => void
}

/** Status filter + created-at sort + URL-persisted pagination over the runs. */
function useExecutionsView(executions: readonly WorkflowExecution[]): ExecutionsView {
  const [statusFilter, setStatusFilter] = useState<ExecStatusFilter>('all')
  const [sortDir, setSortDir] = useState<ExecSortDir>('desc')

  const processed = useMemo(() => {
    const filtered =
      statusFilter === 'all'
        ? executions
        : executions.filter((r) => r.status === statusFilter)
    const sign = sortDir === 'asc' ? 1 : -1
    return [...filtered].sort(
      (a, b) => (new Date(a.created_at).getTime() - new Date(b.created_at).getTime()) * sign,
    )
  }, [executions, statusFilter, sortDir])

  const { page, pageSize, totalItems, paginatedItems, setPage, setPageSize, resetPage } =
    useListPagination({ items: processed, namespace: 'executions' })

  // Filter / sort changes narrow the list, so return to page 1.
  useEffect(() => {
    resetPage()
  }, [statusFilter, sortDir, resetPage])

  const toggleSort = useCallback(() => setSortDir((d) => (d === 'asc' ? 'desc' : 'asc')), [])

  return {
    statusFilter, setStatusFilter, sortDir, toggleSort, processed,
    page, pageSize, totalItems, paginatedItems, setPage, setPageSize,
  }
}

const TERMINAL_STATUSES = new Set<WorkflowExecution['status']>([
  'completed',
  'failed',
  'cancelled',
])

// Map the workflow execution lifecycle onto the four-tone AgentRuntimeStatus
// that ``StatusBadge`` understands. ``running`` maps to active (live, in flight),
// ``completed`` to active too (terminal-success), ``failed`` to error,
// ``cancelled`` to offline (terminal-but-not-an-error), ``pending`` to idle.
const STATUS_BADGE_MAP: Record<WorkflowExecution['status'], AgentRuntimeStatus> = {
  pending: 'idle',
  running: 'active',
  completed: 'active',
  failed: 'error',
  cancelled: 'offline',
}

export default function WorkflowExecutionsPage() {
  const { id } = useParams<{ id: string }>()
  const ctrl = useWorkflowExecutionsController(id)
  const view = useExecutionsView(ctrl.executions)

  if (!id) {
    return (
      <div className="space-y-section-gap">
        <Breadcrumbs
          items={[{ label: 'Workflows', to: ROUTES.WORKFLOWS }, { label: 'Executions' }]}
        />
        <ErrorBanner severity="error" title="Missing workflow id in URL" />
      </div>
    )
  }

  return (
    <div className="space-y-section-gap">
      <Breadcrumbs
        items={[
          { label: 'Workflows', to: ROUTES.WORKFLOWS },
          { label: id },
          { label: 'Executions' },
        ]}
      />
      <ListHeader title="Workflow executions" count={ctrl.executions.length} />

      {ctrl.error && (
        <ErrorBanner
          severity="error"
          title="Could not load executions"
          description={ctrl.error}
          onRetry={() => void ctrl.reload()}
        />
      )}

      {ctrl.executions.length > 0 && (
        <ExecutionControls
          statusFilter={view.statusFilter}
          onStatusFilterChange={view.setStatusFilter}
          sortDir={view.sortDir}
          onToggleSort={view.toggleSort}
        />
      )}

      <ExecutionsListBody
        loading={ctrl.loading}
        error={ctrl.error}
        workflowId={id}
        hasAnyExecutions={ctrl.executions.length > 0}
        executions={view.paginatedItems}
        onCancelClick={ctrl.setPendingCancel}
      />

      {view.processed.length > 0 && (
        <Pagination
          page={view.page}
          pageSize={view.pageSize}
          total={view.totalItems}
          onPageChange={view.setPage}
          onPageSizeChange={view.setPageSize}
        />
      )}

      <CancelConfirmDialog
        pendingCancel={ctrl.pendingCancel}
        onClose={() => ctrl.setPendingCancel(null)}
        onConfirm={ctrl.handleCancel}
      />
    </div>
  )
}

interface ExecutionControlsProps {
  statusFilter: ExecStatusFilter
  onStatusFilterChange: (value: ExecStatusFilter) => void
  sortDir: ExecSortDir
  onToggleSort: () => void
}

function ExecutionControls({
  statusFilter,
  onStatusFilterChange,
  sortDir,
  onToggleSort,
}: ExecutionControlsProps) {
  return (
    <SearchFilterSort
      filters={
        <SegmentedControl
          label="Filter by status"
          value={statusFilter}
          onChange={onStatusFilterChange}
          options={STATUS_FILTER_OPTIONS}
          size="sm"
        />
      }
      sort={
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={onToggleSort}
          aria-label={sortDir === 'asc' ? 'Sort newest first' : 'Sort oldest first'}
        >
          {sortDir === 'asc' ? (
            <ArrowUpWideNarrow className="size-4" aria-hidden="true" />
          ) : (
            <ArrowDownWideNarrow className="size-4" aria-hidden="true" />
          )}
          {sortDir === 'asc' ? 'Oldest first' : 'Newest first'}
        </Button>
      }
    />
  )
}

interface ExecutionsListBodyProps {
  loading: boolean
  error: string | null
  workflowId: string
  hasAnyExecutions: boolean
  executions: readonly WorkflowExecution[]
  onCancelClick: (executionId: string) => void
}

function ExecutionsListBody({
  loading,
  error,
  workflowId,
  hasAnyExecutions,
  executions,
  onCancelClick,
}: ExecutionsListBodyProps) {
  if (loading && executions.length === 0) {
    return (
      <ProgressIndicator
        variant="indeterminate"
        label="Loading executions"
        description={`Fetching run history for ${workflowId}`}
      />
    )
  }
  if (!error && executions.length === 0) {
    // Distinguish a filter that matched nothing ("runs exist, none match")
    // from a workflow that has never run, so the empty copy is not misleading.
    return (
      <EmptyState
        title={hasAnyExecutions ? 'No matching executions' : 'No executions yet'}
        description={
          hasAnyExecutions
            ? 'Try changing the status filter or sort.'
            : 'Trigger this workflow to see its run history here.'
        }
      />
    )
  }
  if (executions.length === 0) return null
  return (
    <SectionCard title="Recent runs">
      <ul className="divide-y divide-border">
        {executions.map((row) => (
          <ExecutionListItem key={row.id} row={row} onCancelClick={onCancelClick} />
        ))}
      </ul>
    </SectionCard>
  )
}

interface ExecutionListItemProps {
  row: WorkflowExecution
  onCancelClick: (executionId: string) => void
}

function ExecutionListItem({ row, onCancelClick }: ExecutionListItemProps) {
  const inFlight = !TERMINAL_STATUSES.has(row.status)
  return (
    <li className="flex items-center gap-4 py-2">
      <span className="font-mono text-xs text-foreground">{row.id.slice(0, 8)}</span>
      <StatusBadge status={STATUS_BADGE_MAP[row.status]} decorative />
      <span className="text-xs uppercase tracking-wide text-text-secondary">
        {row.status}
      </span>
      <span className="flex-1 text-xs text-text-secondary">
        {`Started ${formatDateTime(row.created_at)}`}
      </span>
      {row.error && (
        <span className="truncate text-xs text-danger" title={row.error}>
          {row.error}
        </span>
      )}
      {inFlight && (
        <Button type="button" size="xs" variant="outline" onClick={() => onCancelClick(row.id)}>
          Cancel
        </Button>
      )}
    </li>
  )
}

interface CancelConfirmDialogProps {
  pendingCancel: string | null
  onClose: () => void
  onConfirm: (executionId: string) => Promise<void>
}

function CancelConfirmDialog({
  pendingCancel,
  onClose,
  onConfirm,
}: CancelConfirmDialogProps) {
  return (
    <ConfirmDialog
      open={pendingCancel !== null}
      title="Cancel execution?"
      description="The execution will stop at the next available checkpoint. This is best-effort and not always immediate."
      variant="destructive"
      confirmLabel="Cancel run"
      onOpenChange={(next) => {
        if (!next) onClose()
      }}
      onConfirm={async () => {
        // Capture the target id before awaiting so we can compare against
        // the latest state after the cancel resolves. Without this, closing
        // would also close a NEWLY opened dialog if the user re-targeted a
        // different execution while the previous cancel was still in flight.
        const target = pendingCancel
        if (!target) return
        await onConfirm(target)
        onClose()
      }}
    />
  )
}
