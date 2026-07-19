import { paginateAll } from '@/api/client'
import {
  checkConnectionHealth,
  getConnectionTypes,
  listConnections,
} from '@/api/endpoints/connections'
import { listIntegrationHealth } from '@/api/endpoints/integration-health'
import type { HealthReport } from '@/api/types/integrations'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import type {
  ConnectionsGet,
  ConnectionsSet,
  ConnectionsState,
  ConnectionSortKey,
} from './types'

const log = createLogger('connections')

let _listRequestId = 0
let _typesRequestId = 0

/**
 * Invalidate any in-flight connections/type fetch so a late response cannot
 * repopulate a cleared store. ``reset()`` calls this before wiping state, so a
 * fetch that resolves after a reset (or a newer session's fetch) drops its
 * write instead of resurrecting stale data.
 */
export function invalidateInFlightConnectionRequests(): void {
  _listRequestId += 1
  _typesRequestId += 1
}

async function fetchConnectionTypesImpl(
  set: ConnectionsSet,
  get: ConnectionsGet,
): Promise<void> {
  // The connection-type registry is small and stable; skip the re-fetch once
  // loaded so opening the form modal repeatedly does not re-hit the backend.
  // Still a pure API consumer: nothing is persisted client-side, so a reload
  // re-hydrates it.
  if (get().connectionTypes.length > 0 || get().typesLoading) return
  const requestId = ++_typesRequestId
  const isLatest = () => requestId === _typesRequestId
  set({ typesLoading: true, typesError: null })
  try {
    const connectionTypes = await getConnectionTypes()
    // A reset (or a newer fetch) mid-flight bumps the id; drop the write so it
    // can't repopulate a cleared registry.
    if (!isLatest()) return
    set({ connectionTypes })
  } catch (err) {
    if (!isLatest()) return
    log.error('Failed to fetch connection types:', getErrorMessage(err))
    set({ typesError: getErrorMessage(err) })
  } finally {
    if (isLatest()) set({ typesLoading: false })
  }
}

type ListActionSlice = Pick<
  ConnectionsState,
  | 'fetchConnections'
  | 'fetchConnectionTypes'
  | 'runHealthCheck'
  | 'setSearchQuery'
  | 'setTypeFilter'
  | 'setHealthFilter'
  | 'setSortBy'
  | 'setSortDirection'
>

export function createListActions(
  set: ConnectionsSet,
  get: ConnectionsGet,
): ListActionSlice {
  return {
    fetchConnections: async () => {
      const requestId = ++_listRequestId
      set({ listLoading: true, listError: null })
      const isLatest = () => requestId === _listRequestId
      try {
        const [connections, healthReports] = await Promise.all([
          listConnections(),
          paginateAll<HealthReport>((cursor) =>
            listIntegrationHealth({ cursor, limit: 200 }),
          ).catch((err: unknown) => {
            log.warn('Health aggregate fetch failed:', getErrorMessage(err))
            return null
          }),
        ])
        if (!isLatest()) return
        const prevHealthMap = get().healthMap
        const healthMap: Record<string, HealthReport> = { ...prevHealthMap }
        if (healthReports !== null) {
          for (const report of healthReports) {
            healthMap[report.connection_name] = report
          }
        }
        set({ connections, healthMap })
      } catch (err) {
        if (!isLatest()) return
        log.error('Failed to fetch connections:', getErrorMessage(err))
        set({ listError: getErrorMessage(err) })
      } finally {
        // Always clear ``listLoading`` for the latest request so an
        // overlapping fetch can't strand the skeleton on.
        if (isLatest()) set({ listLoading: false })
      }
    },

    fetchConnectionTypes: () => fetchConnectionTypesImpl(set, get),

    runHealthCheck: async (name: string) => {
      const current = get().checkingHealth
      if (current.includes(name)) return
      set({ checkingHealth: [...current, name] })
      try {
        const report = await checkConnectionHealth(name)
        const state = get()
        set({
          healthMap: { ...state.healthMap, [name]: report },
          checkingHealth: state.checkingHealth.filter((n) => n !== name),
        })
      } catch (err) {
        log.warn(
          'Health check failed for connection',
          sanitizeForLog({ connection: name, error: getErrorMessage(err) }),
        )
        const state = get()
        set({
          checkingHealth: state.checkingHealth.filter((n) => n !== name),
        })
        // Surface the failure to the operator: without a toast the
        // spinner just disappears and they cannot tell whether the
        // probe ran.
        useToastStore.getState().add({
          variant: 'error',
          title: 'Health check failed',
          description: `${name}: ${getErrorMessage(err)}`,
        })
      }
    },

    setSearchQuery: (q: string) => set({ searchQuery: q }),
    setTypeFilter: (t: ConnectionsState['typeFilter']) =>
      set({ typeFilter: t }),
    setHealthFilter: (h: ConnectionsState['healthFilter']) =>
      set({ healthFilter: h }),
    setSortBy: (key: ConnectionSortKey) => set({ sortBy: key }),
    setSortDirection: (dir: 'asc' | 'desc') => set({ sortDirection: dir }),
  }
}
