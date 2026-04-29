import { useEffect, useState } from 'react'

import { getCapabilities } from '@/api/endpoints/capabilities'
import type { Capabilities } from '@/api/types/capabilities'
import { createLogger } from '@/lib/logger'

const log = createLogger('useCapabilities')

/**
 * Module-level cache so multiple consumers share one network call.
 *
 * Capabilities are static for the lifetime of the backend process,
 * so caching across the whole session is correct -- the only way
 * the matrix changes is a restart, which also reloads the SPA.
 */
let _cache: Capabilities | null = null
let _inflight: Promise<Capabilities> | null = null

const ALL_FALSE: Capabilities = {
  simulations: false,
  requests: false,
  ontology: false,
  tunnel: false,
  webhooks: false,
  a2a: false,
  telemetry: false,
  integrations: false,
}

/**
 * Read the runtime capability matrix from ``GET /api/v1/capabilities``.
 *
 * Returns ``ALL_FALSE`` while the first fetch is in flight so pages
 * that gate polling on a flag default to "feature unavailable"
 * during the brief loading window. Pages that need to distinguish
 * loading from "feature off" should read ``loading`` directly.
 */
export function useCapabilities(): {
  capabilities: Capabilities
  loading: boolean
  error: string | null
} {
  const [capabilities, setCapabilities] = useState<Capabilities>(
    () => _cache ?? ALL_FALSE,
  )
  const [loading, setLoading] = useState<boolean>(_cache === null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (_cache !== null) {
      setCapabilities(_cache)
      setLoading(false)
      return
    }
    let cancelled = false
    if (_inflight === null) {
      _inflight = getCapabilities()
    }
    void _inflight
      .then((result) => {
        _cache = result
        if (!cancelled) {
          setCapabilities(result)
          setError(null)
          setLoading(false)
        }
      })
      .catch((err: unknown) => {
        log.error('capabilities_fetch_failed', err)
        if (!cancelled) {
          setError('Failed to load capability matrix.')
          setLoading(false)
        }
      })
      .finally(() => {
        _inflight = null
      })
    return () => {
      cancelled = true
    }
  }, [])

  return { capabilities, loading, error }
}

/**
 * Test-only helper to clear the module-level cache between tests.
 *
 * The hook uses a module-scoped ``_cache`` so multiple consumers
 * share a single network round-trip; that cache leaks across
 * tests in the same Vitest worker. Tests that need a clean state
 * call this in a ``beforeEach``.
 */
export function _resetCapabilitiesCacheForTesting(): void {
  _cache = null
  _inflight = null
}
