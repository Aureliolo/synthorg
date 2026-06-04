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

const HEALTH_ORDER: Record<ConnectionHealthStatus, number> = {
  unhealthy: 0,
  degraded: 1,
  unknown: 2,
  healthy: 3,
}

function _effectiveHealth(
  conn: Connection,
  healthMap: Record<string, HealthReport>,
): ConnectionHealthStatus {
  return healthMap[conn.name]?.status ?? conn.health_status
}

type ConnComparator = (
  a: Connection,
  b: Connection,
  healthMap: Record<string, HealthReport>,
) => number

/**
 * Comparator per `ConnectionSortKey`. `satisfies` keeps the table
 * exhaustive: a new sort key added to the type breaks the build until
 * a comparator is supplied here.
 */
const CONN_COMPARATORS = {
  name: (a, b) => a.name.localeCompare(b.name),
  type: (a, b) => a.connection_type.localeCompare(b.connection_type),
  created_at: (a, b) => a.created_at.localeCompare(b.created_at),
  health: (a, b, h) =>
    HEALTH_ORDER[_effectiveHealth(a, h)] - HEALTH_ORDER[_effectiveHealth(b, h)],
} as const satisfies Record<ConnectionSortKey, ConnComparator>

function sortConnections(
  connections: readonly Connection[],
  healthMap: Record<string, HealthReport>,
  sortBy: ConnectionSortKey,
  direction: 'asc' | 'desc',
): readonly Connection[] {
  const compare = CONN_COMPARATORS[sortBy]
  const multiplier = direction === 'asc' ? 1 : -1
  return [...connections].sort((a, b) => compare(a, b, healthMap) * multiplier)
}

interface FilterCriteria {
  readonly healthMap: Record<string, HealthReport>
  readonly typeFilter: string | null
  readonly healthFilter: ConnectionHealthStatus | null
  readonly query: string
}

function _matchesFilters(conn: Connection, criteria: FilterCriteria): boolean {
  if (criteria.typeFilter !== null && conn.connection_type !== criteria.typeFilter) {
    return false
  }
  if (criteria.healthFilter !== null) {
    if (_effectiveHealth(conn, criteria.healthMap) !== criteria.healthFilter) return false
  }
  if (criteria.query.length > 0) {
    return conn.name.toLowerCase().includes(criteria.query)
  }
  return true
}

function filterConnections(
  connections: readonly Connection[],
  criteria: FilterCriteria,
): readonly Connection[] {
  return connections.filter((conn) => _matchesFilters(conn, criteria))
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

  const { start, stop } = polling
  useEffect(() => {
    start()
    return () => stop()
  }, [start, stop])

  const filteredConnections = useMemo(() => {
    const filtered = filterConnections(connections, {
      healthMap,
      typeFilter,
      healthFilter,
      query: searchQuery.trim().toLowerCase(),
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
