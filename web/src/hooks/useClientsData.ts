import { useCallback, useEffect, useState } from 'react'

import { listClients, type ClientProfile } from '@/api/endpoints/clients'
import { createLogger } from '@/lib/logger'
import { useWebSocketStore } from '@/stores/websocket'
import { getErrorMessage } from '@/utils/errors'

const log = createLogger('useClientsData')

/**
 * Loads the paginated client list.
 *
 * Returns the current client snapshot plus connection state so the
 * consumer can surface loading, error, and stale-feed banners.
 */
export function useClientsData(): {
  clients: readonly ClientProfile[]
  loading: boolean
  error: string | null
  wsConnected: boolean
  reload: () => Promise<void>
} {
  const [clients, setClients] = useState<readonly ClientProfile[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const wsConnected = useWebSocketStore((s) => s.connected)

  const reload = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await listClients({ limit: 100 })
      setClients(result.data)
    } catch (err) {
      log.error('list_clients_failed', err)
      setError(`Could not load clients: ${getErrorMessage(err)}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  return { clients, loading, error, wsConnected, reload }
}
