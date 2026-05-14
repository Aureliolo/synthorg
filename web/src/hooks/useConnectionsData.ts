import { useCallback, useEffect, useMemo } from 'react'
import type {
  Connection,
  ConnectionHealthStatus,
  HealthReport,
} from '@/api/types/integrations'
import { usePolling } from '@/hooks/usePolling'
import { useConnectionsStore } from '@/stores/connections'
import type { ConnectionSortKey } from '@/stores/connections/types'

const CONNECTIONS_POLL_INTERVAL_MS = 30_000

export interface UseConnectionsDataReturn {
  connections: readonly Connection[]
  filteredConnections: readonly Connection[]
  healthMap: Record<string, HealthReport>
  loading: boolean
  error: string | null
  checkingHealth: readonly string[]
}

function sortConnections(
  connections: readonly Connection[],
  healthMap: Record<string, HealthReport>,
  sortBy: ConnectionSortKey,
  direction: 'asc' | 'desc',
): readonly Connection[] {
  const sorted = [...connections]
  const multiplier = direction === 'asc' ? 1 : -1
  const healthOrder: Record<ConnectionHealthStatus, number> = {
    unhealthy: 0,
    degraded: 1,
    unknown: 2,
    healthy: 3,
  }
  sorted.sort((a, b) => {
    switch (sortBy) {
      case 'name':
        return a.name.localeCompare(b.name) * multiplier
      case 'type':
        return a.connection_type.localeCompare(b.connection_type) * multiplier
      case 'created_at':
        return (a.created_at ?? '').localeCompare(b.created_at ?? '') * multiplier
      case 'health': {
        const aHealth = healthMap[a.name]?.status ?? a.health_status ?? 'unknown'
        const bHealth = healthMap[b.name]?.status ?? b.health_status ?? 'unknown'
        return (healthOrder[aHealth] - healthOrder[bHealth]) * multiplier
      }
      default:
        return 0
    }
  })
  return sorted
}

export function useConnectionsData(): UseConnectionsDataReturn {
  const connections = useConnectionsStore((s) => s.connections)
  const healthMap = useConnectionsStore((s) => s.healthMap)
  const loading = useConnectionsStore((s) => s.listLoading)
  const error = useConnectionsStore((s) => s.listError)
  const checkingHealth = useConnectionsStore((s) => s.checkingHealth)
  const searchQuery = useConnectionsStore((s) => s.searchQuery)
  const typeFilter = useConnectionsStore((s) => s.typeFilter)
  const healthFilter = useConnectionsStore((s) => s.healthFilter)
  const sortBy = useConnectionsStore((s) => s.sortBy)
  const sortDirection = useConnectionsStore((s) => s.sortDirection)

  const pollFn = useCallback(async () => {
    await useConnectionsStore.getState().fetchConnections()
  }, [])
  const polling = usePolling(pollFn, CONNECTIONS_POLL_INTERVAL_MS)

  useEffect(() => {
    polling.start()
    return () => polling.stop()
    // eslint-disable-next-line @eslint-react/exhaustive-deps
  }, [])

  const filteredConnections = useMemo(() => {
    const normalizedQuery = searchQuery.trim().toLowerCase()
    const filtered = connections.filter((conn) => {
      if (typeFilter !== null && conn.connection_type !== typeFilter) {
        return false
      }
      if (healthFilter !== null) {
        const effectiveHealth = healthMap[conn.name]?.status ?? conn.health_status ?? 'unknown'
        if (effectiveHealth !== healthFilter) return false
      }
      if (normalizedQuery.length > 0) {
        return conn.name.toLowerCase().includes(normalizedQuery)
      }
      return true
    })
    return sortConnections(filtered, healthMap, sortBy, sortDirection)
  }, [connections, healthMap, searchQuery, typeFilter, healthFilter, sortBy, sortDirection])

  return {
    connections,
    filteredConnections,
    healthMap,
    loading,
    error,
    checkingHealth,
  }
}
