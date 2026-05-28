/**
 * Workflow executions list.
 *
 * Lists recent runs for a single workflow definition with a Cancel action for
 * executions still in flight.
 */
import { useParams } from 'react-router'
import { Breadcrumbs } from '@/components/ui/breadcrumbs'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { ProgressIndicator } from '@/components/ui/progress-indicator'
import { SectionCard } from '@/components/ui/section-card'
import { StatusBadge } from '@/components/ui/status-badge'
import type { AgentRuntimeStatus } from '@/lib/utils'
import { ROUTES } from '@/router/routes'
import { formatDateTime } from '@/utils/format'
import type { WorkflowExecution } from '@/api/endpoints/workflow-executions'

import { useWorkflowExecutionsController } from './workflows/useWorkflowExecutionsController'

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

      <ExecutionsListBody
        loading={ctrl.loading}
        error={ctrl.error}
        workflowId={id}
        executions={ctrl.executions}
        onCancelClick={ctrl.setPendingCancel}
      />

      <CancelConfirmDialog
        pendingCancel={ctrl.pendingCancel}
        onClose={() => ctrl.setPendingCancel(null)}
        onConfirm={ctrl.handleCancel}
      />
    </div>
  )
}

interface ExecutionsListBodyProps {
  loading: boolean
  error: string | null
  workflowId: string
  executions: readonly WorkflowExecution[]
  onCancelClick: (executionId: string) => void
}

function ExecutionsListBody({
  loading,
  error,
  workflowId,
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
    return (
      <EmptyState
        title="No executions yet"
        description="Trigger this workflow to see its run history here."
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
