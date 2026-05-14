import { useEffect, useMemo, useCallback } from 'react'
import { useWorkflowsStore } from '@/stores/workflows'
import { usePolling } from '@/hooks/usePolling'
import type { WorkflowDefinition } from '@/api/types/workflows'

const WORKFLOWS_POLL_INTERVAL = 30_000

export interface UseWorkflowsDataReturn {
  workflows: readonly WorkflowDefinition[]
  filteredWorkflows: readonly WorkflowDefinition[]
  totalWorkflows: number
  loading: boolean
  error: string | null
}

export function useWorkflowsData(): UseWorkflowsDataReturn {
  const workflows = useWorkflowsStore((s) => s.workflows)
  const totalWorkflows = useWorkflowsStore((s) => s.totalWorkflows)
  const loading = useWorkflowsStore((s) => s.listLoading)
  const error = useWorkflowsStore((s) => s.listError)
  const searchQuery = useWorkflowsStore((s) => s.searchQuery)
  const workflowTypeFilter = useWorkflowsStore((s) => s.workflowTypeFilter)

  useEffect(() => {
    void useWorkflowsStore.getState().fetchWorkflows()
  }, [])

  const pollFn = useCallback(async () => {
    await useWorkflowsStore.getState().fetchWorkflows()
  }, [])
  const polling = usePolling(pollFn, WORKFLOWS_POLL_INTERVAL)

  useEffect(() => {
    polling.start()
    return () => polling.stop()
    // eslint-disable-next-line @eslint-react/exhaustive-deps -- polling object is stable
  }, [])

  const filteredWorkflows = useMemo(() => {
    let result = [...workflows]
    if (searchQuery) {
      const q = searchQuery.toLowerCase()
      result = result.filter(
        (w) =>
          w.name.toLowerCase().includes(q) ||
          w.description.toLowerCase().includes(q) ||
          w.id.toLowerCase().includes(q),
      )
    }
    if (workflowTypeFilter) {
      result = result.filter((w) => w.workflow_type === workflowTypeFilter)
    }
    return result
  }, [workflows, searchQuery, workflowTypeFilter])

  return {
    workflows,
    filteredWorkflows,
    totalWorkflows,
    loading,
    error,
  }
}
