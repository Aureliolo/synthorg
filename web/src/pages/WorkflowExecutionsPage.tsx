/**
 * Workflow executions list.
 *
 * Lists recent runs for a single workflow definition with a Cancel
 * action for executions still in flight.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router'
import { Breadcrumbs } from '@/components/ui/breadcrumbs'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { ProgressIndicator } from '@/components/ui/progress-indicator'
import { SectionCard } from '@/components/ui/section-card'
import { useToastStore } from '@/stores/toast'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { ROUTES } from '@/router/routes'
import { formatDateTime } from '@/utils/format'
import { getErrorMessage } from '@/utils/errors'
import {
  cancelWorkflowExecution,
  listWorkflowExecutions,
  type WorkflowExecution,
} from '@/api/endpoints/workflow-executions'

const log = createLogger('WorkflowExecutionsPage')

const TERMINAL_STATUSES = new Set<WorkflowExecution['status']>([
  'completed',
  'failed',
  'cancelled',
])

export default function WorkflowExecutionsPage() {
  const { id } = useParams<{ id: string }>()
  const [executions, setExecutions] = useState<readonly WorkflowExecution[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [pendingCancel, setPendingCancel] = useState<string | null>(null)
  const addToast = useToastStore((s) => s.add)
  // Per-request sequence number, bumped on every reload. The earlier
  // ``requestedFor !== id`` guard compared two values from the same
  // closure snapshot, so it never filtered out an older in-flight
  // request after the user switched workflows. A ref-backed counter
  // is the canonical pattern: the in-flight request remembers its
  // own id, the ref always reads the latest issued one, and a
  // mismatch unambiguously identifies a stale response.
  const requestSeqRef = useRef(0)

  // When the operator navigates between workflows quickly (URL
  // changes drive ``id``), an in-flight response for the previous
  // workflow could otherwise land in the new workflow's table.
  // ``setExecutions([])`` clears the previous workflow's rows
  // immediately so the operator sees a clean transition instead of
  // stale data + spinner; the sequence guard drops responses whose
  // captured ``requestId`` no longer matches the latest issued one.
  const reload = useCallback(async () => {
    if (!id) return
    const requestedFor = id
    requestSeqRef.current += 1
    const requestId = requestSeqRef.current
    setExecutions([])
    setLoading(true)
    setError(null)
    try {
      const rows = await listWorkflowExecutions(requestedFor)
      if (requestId !== requestSeqRef.current) return
      setExecutions(rows)
    } catch (err) {
      if (requestId !== requestSeqRef.current) return
      const message = getErrorMessage(err)
      // SEC-1: workflowId is URL-controlled, sanitize.
      log.error('listWorkflowExecutions failed', {
        workflowId: sanitizeForLog(requestedFor),
        error: sanitizeForLog(message),
      })
      setError(message)
    } finally {
      if (requestId === requestSeqRef.current) setLoading(false)
    }
  }, [id])

  useEffect(() => {
    void reload()
  }, [reload])

  // Drop any in-flight cancel target when the workflow id changes;
  // otherwise navigating away while the confirm dialog is open
  // would let the user click Cancel and dispatch the request
  // against a different workflow's execution id. Deferred to a
  // microtask so the effect stays free of synchronous setState
  // (per the ESLint set-state-in-effect rule).
  useEffect(() => {
    let cancelled = false
    void Promise.resolve().then(() => {
      if (cancelled) return
      setPendingCancel(null)
    })
    return () => { cancelled = true }
  }, [id])

  const handleCancel = useCallback(async (executionId: string) => {
    try {
      await cancelWorkflowExecution(executionId)
      addToast({ variant: 'success', title: 'Cancellation requested' })
      void reload()
    } catch (err) {
      const message = getErrorMessage(err)
      log.error('cancelWorkflowExecution failed', {
        executionId: sanitizeForLog(executionId),
        error: sanitizeForLog(message),
      })
      addToast({
        variant: 'error',
        title: 'Could not cancel execution',
        description: message,
      })
    }
  }, [addToast, reload])

  if (!id) {
    return (
      <div className="space-y-section-gap">
        <Breadcrumbs items={[{ label: 'Workflows', to: ROUTES.WORKFLOWS }, { label: 'Executions' }]} />
        <ErrorBanner severity="error" title="Missing workflow id in URL" />
      </div>
    )
  }

  return (
    <div className="space-y-section-gap">
      <Breadcrumbs items={[{ label: 'Workflows', to: ROUTES.WORKFLOWS }, { label: id }, { label: 'Executions' }]} />
      <ListHeader title="Workflow executions" count={executions.length} />

      {error && (
        <ErrorBanner
          severity="error"
          title="Could not load executions"
          description={error}
          onRetry={() => void reload()}
        />
      )}

      {loading && executions.length === 0 ? (
        <ProgressIndicator
          variant="indeterminate"
          label="Loading executions"
          description={`Fetching run history for ${id}`}
        />
      ) : !error && executions.length === 0 ? (
        <EmptyState
          title="No executions yet"
          description="Trigger this workflow to see its run history here."
        />
      ) : executions.length > 0 ? (
        <SectionCard title="Recent runs">
          <ul className="divide-y divide-border">
            {executions.map((row) => {
              const inFlight = !TERMINAL_STATUSES.has(row.status)
              return (
                <li key={row.id} className="flex items-center gap-4 py-2">
                  <span className="font-mono text-xs text-foreground">{row.id.slice(0, 8)}</span>
                  <span className="rounded-md border border-border bg-card px-2 py-0.5 text-xs uppercase tracking-wide text-text-secondary">
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
                    <Button
                      type="button"
                      size="xs"
                      variant="outline"
                      onClick={() => setPendingCancel(row.id)}
                    >
                      Cancel
                    </Button>
                  )}
                </li>
              )
            })}
          </ul>
        </SectionCard>
      ) : null}

      <ConfirmDialog
        open={pendingCancel !== null}
        title="Cancel execution?"
        description="The execution will stop at the next available checkpoint. This is best-effort and not always immediate."
        variant="destructive"
        confirmLabel="Cancel run"
        onOpenChange={(next) => { if (!next) setPendingCancel(null) }}
        onConfirm={async () => {
          if (pendingCancel) {
            await handleCancel(pendingCancel)
            setPendingCancel(null)
          }
        }}
      />
    </div>
  )
}
