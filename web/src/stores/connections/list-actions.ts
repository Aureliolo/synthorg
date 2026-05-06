import { paginateAll } from '@/api/client'
import {
  checkConnectionHealth,
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

export function createListActions(set: ConnectionsSet, get: ConnectionsGet) {
  return {
    fetchConnections: async () => {
      const requestId = ++_listRequestId
      set({ listLoading: true, listError: null })
      try {
        const [connections, healthReports] = await Promise.all([
          listConnections(),
          paginateAll<HealthReport>((cursor) =>
            listIntegrationHealth({ cursor, limit: 200 }),
          ).catch((err) => {
            log.warn('Health aggregate fetch failed:', getErrorMessage(err))
            return null
          }),
        ])
        if (requestId !== _listRequestId) return
        const prevHealthMap = get().healthMap
        const healthMap: Record<string, HealthReport> = { ...prevHealthMap }
        if (healthReports !== null) {
          for (const report of healthReports) {
            healthMap[report.connection_name] = report
          }
        }
        set({ connections, healthMap, listLoading: false })
      } catch (err) {
        if (requestId !== _listRequestId) return
        log.error('Failed to fetch connections:', getErrorMessage(err))
        set({
          listLoading: false,
          listError: getErrorMessage(err),
        })
      }
    },

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
