import { useCallback, useEffect, useRef, useState } from 'react'
import {
  cancelWorkflowExecution,
  listWorkflowExecutions,
  type WorkflowExecution,
} from '@/api/endpoints/workflow-executions'
import { useToastStore } from '@/stores/toast'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { getErrorMessage } from '@/utils/errors'

const log = createLogger('WorkflowExecutionsPage')

export interface WorkflowExecutionsController {
  executions: readonly WorkflowExecution[]
  loading: boolean
  error: string | null
  pendingCancel: string | null
  reload: () => Promise<void>
  setPendingCancel: (id: string | null) => void
  handleCancel: (executionId: string) => Promise<void>
}

export function useWorkflowExecutionsController(
  id: string | undefined,
): WorkflowExecutionsController {
  const [executions, setExecutions] = useState<readonly WorkflowExecution[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [pendingCancel, setPendingCancel] = useState<string | null>(null)
  const addToast = useToastStore((s) => s.add)
  // Per-request sequence number, bumped on every reload. A ref-backed counter
  // unambiguously identifies a stale response when the user switches workflows.
  const requestSeqRef = useRef(0)
  // Latest workflow id, mirrored into a ref so the in-flight reload can
  // compare against the most recent route param at settle time.
  const latestIdRef = useRef<string | undefined>(id)
  latestIdRef.current = id

  const reload = useCallback(async () => {
    if (!id) return
    const requestedFor = id
    requestSeqRef.current += 1
    const requestId = requestSeqRef.current
    setExecutions([])
    setLoading(true)
    setError(null)
    const isStale = () =>
      requestId !== requestSeqRef.current || latestIdRef.current !== requestedFor
    try {
      const result = await listWorkflowExecutions(requestedFor)
      if (isStale()) return
      setExecutions(result.data)
    } catch (err) {
      if (isStale()) return
      const message = getErrorMessage(err)
      // SEC-1: workflowId is URL-controlled, sanitize.
      log.error('listWorkflowExecutions failed', {
        workflowId: sanitizeForLog(requestedFor),
        error: sanitizeForLog(message),
      })
      setError(message)
    } finally {
      if (!isStale()) setLoading(false)
    }
  }, [id])

  useEffect(() => {
    void reload()
  }, [reload])

  // Drop any in-flight cancel target when the workflow id changes. setState
  // fires synchronously here on purpose: a deferred microtask creates a race
  // where the user can click Confirm on the dialog (still open with the
  // previous workflow's execution id) before the microtask settles.
  useEffect(() => {
    // eslint-disable-next-line @eslint-react/set-state-in-effect -- correctness wins over the microtask defer here
    setPendingCancel(null)
  }, [id])

  const handleCancel = useCallback(
    async (executionId: string) => {
      const issuedFor = id
      try {
        await cancelWorkflowExecution(executionId)
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
        return
      }
      addToast({ variant: 'success', title: 'Cancellation requested' })
      if (latestIdRef.current === issuedFor) {
        void reload()
      }
    },
    [addToast, reload, id],
  )

  return { executions, loading, error, pendingCancel, reload, setPendingCancel, handleCancel }
}
