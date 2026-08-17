import { useEffect, useState } from 'react'

import { getCapabilities } from '@/api/endpoints/capabilities'
import type { Capabilities } from '@/api/types/capabilities'
import { createLogger } from '@/lib/logger'

const log = createLogger('useCapabilities')

/**
 * Module-level cache so multiple consumers share one network call.
 *
 * Most of the matrix is static for the lifetime of the backend process,
 * so caching across the whole session is correct: the only way those
 * flags change is a restart, which also reloads the SPA. The web-research
 * flags are the exception -- they resolve from settings the operator can
 * write while the dashboard is open -- so `refreshCapabilities()` exists
 * to re-read the matrix and push the result at every mounted consumer.
 */
let _cache: Capabilities | null = null
let _inflight: Promise<Capabilities> | null = null
const _subscribers = new Set<(next: Capabilities) => void>()

/**
 * Monotonic issue counter, so a slower earlier read cannot overwrite a faster
 * later one. The mount fetch and `refreshCapabilities` are independent
 * requests: without this, a refresh triggered by an operator fixing web search
 * can resolve first and then be reverted by the stale mount fetch landing
 * after it, poisoning the cache for every later mount.
 */
let _generation = 0

const ALL_FALSE: Capabilities = {
  simulations: false,
  requests: false,
  ontology: false,
  tunnel: false,
  webhooks: false,
  a2a: false,
  telemetry: false,
  integrations: false,
  web_search: false,
  web_search_blocker: 'disabled',
  web_search_message: '',
  web_search_notify: false,
  web_search_reusable_connections: [],
  web_fetch: false,
}

/**
 * Re-read the capability matrix and hand the result to every consumer.
 *
 * Call this after writing a setting the matrix reports on. Without it the
 * session cache would keep serving the pre-write answer, so an operator who
 * had just fixed web search would go on being told it was broken.
 */
export async function refreshCapabilities(): Promise<void> {
  const issued = ++_generation
  try {
    const result = await getCapabilities()
    if (issued !== _generation) return
    _cache = result
    for (const notify of _subscribers) notify(result)
  } catch (err) {
    // Deliberately keeps the cached matrix: a failed re-read says nothing
    // about the features, and blanking it would hide working surfaces.
    log.error('capabilities_refresh_failed', err)
  }
}

/** Drops the cache so the next mount re-fetches. Test teardown hook. */
export function resetCapabilitiesCache(): void {
  _cache = null
  _inflight = null
  _generation = 0
  _subscribers.clear()
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

  // Registered separately from the fetch effect below, which returns early on
  // a cache hit: folding the two would leave a consumer that mounted after the
  // first fetch subscribed to nothing, and `refreshCapabilities` would move
  // every other consumer while that one kept rendering the stale matrix.
  useEffect(() => {
    _subscribers.add(setCapabilities)
    return () => {
      _subscribers.delete(setCapabilities)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    // Cache hit -- skip the network call entirely. Async tick keeps
    // the state setters out of the same synchronous frame as the
    // effect body so eslint-react's set-state-in-effect rule stays
    // happy and React does not schedule an extra render-while-render
    // batch.
    if (_cache !== null) {
      const cached = _cache
      queueMicrotask(() => {
        if (cancelled) return
        setCapabilities(cached)
        setLoading(false)
      })
      return () => {
        cancelled = true
      }
    }
    const issued = ++_generation
    if (_inflight === null) {
      _inflight = getCapabilities()
    }
    void _inflight
      .then((result) => {
        // A refresh issued after this read has already answered with fresher
        // state; letting this one land would revert it.
        if (issued === _generation) _cache = result
        if (!cancelled) {
          setCapabilities(_cache ?? result)
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
          setError('Could not load the list of available features. Refresh to try again.')
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
