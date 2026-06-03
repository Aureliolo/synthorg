/**
 * Composable hook for ontology page data.
 *
 * Fetches entities + drift reports on mount, sets up polling,
 * and provides filtered/sorted data.
 */
import { useCallback, useEffect, useMemo } from 'react'
import type { EntityResponse, DriftReportResponse } from '@/api/endpoints/ontology'
import { usePolling } from '@/hooks/usePolling'
import { useOntologyStore } from '@/stores/ontology'

const POLL_INTERVAL = 30_000

export interface UseOntologyDataReturn {
  entities: readonly EntityResponse[]
  filteredEntities: readonly EntityResponse[]
  totalEntities: number
  entitiesLoading: boolean
  entitiesError: string | null
  driftReports: readonly DriftReportResponse[]
  driftLoading: boolean
  driftError: string | null
  tierFilter: 'all' | 'core' | 'user'
  searchQuery: string
  selectedEntity: EntityResponse | null
  coreCount: number
  userCount: number
}

export function useOntologyData(): UseOntologyDataReturn {
  const entities = useOntologyStore((s) => s.entities)
  const totalEntities = useOntologyStore((s) => s.totalEntities)
  const entitiesLoading = useOntologyStore((s) => s.entitiesLoading)
  const entitiesError = useOntologyStore((s) => s.entitiesError)
  const driftReports = useOntologyStore((s) => s.driftReports)
  const driftLoading = useOntologyStore((s) => s.driftLoading)
  const driftError = useOntologyStore((s) => s.driftError)
  const tierFilter = useOntologyStore((s) => s.tierFilter)
  const searchQuery = useOntologyStore((s) => s.searchQuery)
  const selectedEntity = useOntologyStore((s) => s.selectedEntity)

  // Polling fetches both entities and drift reports
  const pollFn = useCallback(async () => {
    await Promise.all([
      useOntologyStore.getState().fetchEntities(),
      useOntologyStore.getState().fetchDriftReports(),
    ])
  }, [])
  const polling = usePolling(pollFn, POLL_INTERVAL)

  // Initial fetch + start polling
  const { start, stop } = polling
  useEffect(() => {
    void pollFn()
    start()
    return () => stop()
  }, [pollFn, start, stop])

  // Client-side filtering
  const filteredEntities = useMemo(() => {
    let filtered = [...entities]

    if (tierFilter !== 'all') {
      filtered = filtered.filter((e) => e.tier === tierFilter)
    }

    if (searchQuery) {
      const q = searchQuery.toLowerCase()
      filtered = filtered.filter(
        (e) =>
          e.name.toLowerCase().includes(q) ||
          e.definition.toLowerCase().includes(q),
      )
    }

    return filtered
  }, [entities, tierFilter, searchQuery])

  // Counts
  const coreCount = useMemo(
    () => entities.filter((e) => e.tier === 'core').length,
    [entities],
  )
  const userCount = useMemo(
    () => entities.filter((e) => e.tier === 'user').length,
    [entities],
  )

  return {
    entities,
    filteredEntities,
    totalEntities,
    entitiesLoading,
    entitiesError,
    driftReports,
    driftLoading,
    driftError,
    tierFilter,
    searchQuery,
    selectedEntity,
    coreCount,
    userCount,
  }
}
