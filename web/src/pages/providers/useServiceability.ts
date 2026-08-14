/**
 * Reads the per-model serviceability view, scoped to one provider or
 * fleet-wide. Live from the REST API on every mount; nothing is persisted
 * client-side (Pure API Consumer).
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  getFleetServiceability,
  getProviderServiceability,
} from '@/api/endpoints/providers'
import type { ModelServiceability } from '@/api/types/providers'
import { createLogger } from '@/lib/logger'
import { createCancellationToken, type CancellationToken } from '@/utils/cancellation'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'

const log = createLogger('useServiceability')

export interface ServiceabilityState {
  rows: readonly ModelServiceability[]
  loading: boolean
  error: string | null
}

export interface ServiceabilityController {
  state: ServiceabilityState
  load: () => void
}

/** Stable row key: a provider serves many models, a model many providers. */
export function serviceabilityRowKey(row: ModelServiceability): string {
  return `${row.provider_name}/${row.model ?? ''}`
}

/**
 * @param providerName - Scope to one provider, or fleet-wide when undefined.
 */
export function useServiceability(providerName?: string): ServiceabilityController {
  const [state, setState] = useState<ServiceabilityState>({
    rows: [],
    loading: true,
    error: null,
  })

  const tokenRef = useRef<CancellationToken | null>(null)

  const loadWith = useCallback(
    (token: CancellationToken) => {
      setState((prev) => ({ ...prev, loading: true, error: null }))
      const request =
        providerName === undefined
          ? getFleetServiceability()
          : getProviderServiceability(providerName)
      void request
        .then((rows) => {
          if (token.cancelled()) return
          setState({ rows, loading: false, error: null })
        })
        .catch((err: unknown) => {
          if (token.cancelled()) return
          const message = getErrorMessage(err)
          log.error('load serviceability failed', { error: sanitizeForLog(message) })
          setState({ rows: [], loading: false, error: message })
        })
    },
    [providerName],
  )

  const load = useCallback(() => {
    tokenRef.current?.cancel()
    const token = createCancellationToken()
    tokenRef.current = token
    loadWith(token)
  }, [loadWith])

  useEffect(() => {
    // The token is created synchronously so the cleanup below cancels the
    // request this effect started. Minting it inside the deferred callback
    // would let cleanup run first and cancel the previous token, leaving
    // this one live: switching providers quickly would then land the old
    // provider's rows over the new one's.
    const token = createCancellationToken()
    tokenRef.current = token
    void Promise.resolve().then(() => {
      if (token.cancelled()) return
      loadWith(token)
    })
    return () => {
      token.cancel()
    }
  }, [loadWith])

  return { state, load }
}
