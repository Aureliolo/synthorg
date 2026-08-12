/**
 * Reads the per-model serviceability view, scoped to one provider or
 * fleet-wide. Live from the REST API on every mount; nothing is persisted
 * client-side (Pure API Consumer).
 */
import { useCallback, useEffect, useState } from 'react'
import {
  getFleetServiceability,
  getProviderServiceability,
} from '@/api/endpoints/providers'
import type { ModelServiceability } from '@/api/types/providers'
import { createLogger } from '@/lib/logger'
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

  const load = useCallback(() => {
    setState((prev) => ({ ...prev, loading: true, error: null }))
    const request =
      providerName === undefined
        ? getFleetServiceability()
        : getProviderServiceability(providerName)
    void request
      .then((rows) => {
        setState({ rows, loading: false, error: null })
      })
      .catch((err: unknown) => {
        const message = getErrorMessage(err)
        log.error('load serviceability failed', { error: sanitizeForLog(message) })
        setState({ rows: [], loading: false, error: message })
      })
  }, [providerName])

  useEffect(() => {
    void Promise.resolve().then(() => {
      load()
    })
  }, [load])

  return { state, load }
}
