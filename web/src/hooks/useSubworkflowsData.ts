import { useEffect, useMemo, useCallback } from 'react'
import { useSubworkflowsStore } from '@/stores/subworkflows'
import { usePolling } from '@/hooks/usePolling'
import type { SubworkflowSummary } from '@/api/types/workflows'

const SUBWORKFLOWS_POLL_INTERVAL = 30_000

export interface UseSubworkflowsDataReturn {
  subworkflows: readonly SubworkflowSummary[]
  filteredSubworkflows: readonly SubworkflowSummary[]
  loading: boolean
  error: string | null
}

export function useSubworkflowsData(): UseSubworkflowsDataReturn {
  const subworkflows = useSubworkflowsStore((s) => s.subworkflows)
  const loading = useSubworkflowsStore((s) => s.listLoading)
  const error = useSubworkflowsStore((s) => s.listError)
  const searchQuery = useSubworkflowsStore((s) => s.searchQuery)

  const pollFn = useCallback(async () => {
    await useSubworkflowsStore.getState().fetchSubworkflows()
  }, [])
  const polling = usePolling(pollFn, SUBWORKFLOWS_POLL_INTERVAL)

  const { start, stop } = polling
  useEffect(() => {
    start()
    return () => stop()
  }, [start, stop])

  const filteredSubworkflows = useMemo(() => {
    if (!searchQuery) return [...subworkflows]
    const q = searchQuery.toLowerCase()
    return subworkflows.filter(
      (s) =>
        s.name.toLowerCase().includes(q) ||
        s.description.toLowerCase().includes(q) ||
        s.subworkflow_id.toLowerCase().includes(q),
    )
  }, [subworkflows, searchQuery])

  return {
    subworkflows,
    filteredSubworkflows,
    loading,
    error,
  }
}
