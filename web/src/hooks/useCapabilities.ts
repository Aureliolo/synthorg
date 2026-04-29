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
 *
 * On fetch failure the hook surfaces a non-null ``error`` string and
 * leaves ``capabilities`` at its prior value. Callers MUST check
 * ``error`` before treating ``capabilities.<flag> === false`` as
 * "feature not configured" -- a transient 401/500/network error
 * would otherwise be indistinguishable from a deliberately-disabled
 * subsystem and the dashboard would silently hide working features.
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
    // Cache hit -- skip the network call entirely. Async tick keeps
    // the state setters out of the same synchronous frame as the
    // effect body so eslint-react's set-state-in-effect rule stays
    // happy and React does not schedule an extra render-while-render
    // batch.
    if (_cache !== null) {
      const cached = _cache
      queueMicrotask(() => {
        setCapabilities(cached)
        setLoading(false)
      })
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
          // Deliberately do NOT call setCapabilities here: ``error``
          // is the distinct signal callers consume to render an error
          // banner instead of the disabled-capability empty state.
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
