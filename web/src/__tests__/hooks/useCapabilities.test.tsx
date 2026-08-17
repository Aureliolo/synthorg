import { renderHook, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'

import { refreshCapabilities, useCapabilities } from '@/hooks/useCapabilities'
import { successFor } from '@/mocks/handlers'
import { server } from '@/test-setup'
import type { getCapabilities } from '@/api/endpoints/capabilities'
import type { Capabilities } from '@/api/types/capabilities'

/**
 * The matrix is cached for the whole session, which is right for the flags
 * describing wiring fixed at boot and wrong for the web-research flags, which
 * resolve from settings an operator can write with the dashboard open. These
 * cover the refresh path that reconciles the two, and the ordering guard that
 * stops a slow stale read from reverting a fast fresh one.
 */

const BASE: Capabilities = {
  simulations: true,
  requests: true,
  ontology: true,
  tunnel: true,
  webhooks: true,
  a2a: true,
  telemetry: false,
  integrations: true,
  web_search: false,
  web_search_blocker: 'no_provider',
  web_search_message: 'Web search is enabled but no provider is selected.',
  web_search_notify: true,
  web_search_reusable_connections: [],
  web_fetch: true,
}

const FIXED: Capabilities = {
  ...BASE,
  web_search: true,
  web_search_blocker: 'none',
  web_search_message: '',
  web_search_notify: false,
}

function serve(matrix: Capabilities, { delayMs = 0 } = {}) {
  server.use(
    http.get('/api/v1/capabilities/', async () => {
      if (delayMs > 0) await new Promise((resolve) => setTimeout(resolve, delayMs))
      return HttpResponse.json(successFor<typeof getCapabilities>(matrix))
    }),
  )
}

describe('useCapabilities', () => {
  it('reads the matrix on mount', async () => {
    serve(BASE)
    const { result } = renderHook(() => useCapabilities())
    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })
    expect(result.current.capabilities.web_search).toBe(false)
    expect(result.current.error).toBeNull()
  })

  it('surfaces an error rather than reporting every feature off', async () => {
    // A transient failure and a deliberately-disabled subsystem must not look
    // the same, or the dashboard silently hides working surfaces.
    server.use(
      http.get('/api/v1/capabilities/', () => HttpResponse.json({}, { status: 500 })),
    )
    const { result } = renderHook(() => useCapabilities())
    await waitFor(() => {
      expect(result.current.error).not.toBeNull()
    })
  })

  it('pushes a refresh at every mounted consumer', async () => {
    // The behaviour the subscriber set exists for: an operator fixing web
    // search on the Settings page must clear the banner rendered elsewhere.
    serve(BASE)
    const first = renderHook(() => useCapabilities())
    const second = renderHook(() => useCapabilities())
    await waitFor(() => {
      expect(first.result.current.loading).toBe(false)
      expect(second.result.current.loading).toBe(false)
    })
    expect(second.result.current.capabilities.web_search).toBe(false)

    serve(FIXED)
    await refreshCapabilities()

    await waitFor(() => {
      expect(first.result.current.capabilities.web_search).toBe(true)
      expect(second.result.current.capabilities.web_search).toBe(true)
    })
  })

  it('serves the refreshed matrix to a consumer that mounts afterwards', async () => {
    serve(BASE)
    const first = renderHook(() => useCapabilities())
    await waitFor(() => {
      expect(first.result.current.loading).toBe(false)
    })

    serve(FIXED)
    await refreshCapabilities()

    const later = renderHook(() => useCapabilities())
    await waitFor(() => {
      expect(later.result.current.capabilities.web_search).toBe(true)
    })
  })

  it('does not let a slow initial read revert a newer refresh', async () => {
    // The ordering hazard: the mount fetch and the refresh are independent
    // requests, so without a generation guard the one that resolves LAST wins
    // and poisons the cache for every later mount.
    serve(BASE, { delayMs: 60 })
    const mounted = renderHook(() => useCapabilities())

    serve(FIXED)
    await refreshCapabilities()

    await waitFor(() => {
      expect(mounted.result.current.loading).toBe(false)
    })
    expect(mounted.result.current.capabilities.web_search).toBe(true)

    const later = renderHook(() => useCapabilities())
    await waitFor(() => {
      expect(later.result.current.capabilities.web_search).toBe(true)
    })
  })

  it('keeps the cached matrix when a refresh fails', async () => {
    serve(BASE)
    const { result } = renderHook(() => useCapabilities())
    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    server.use(
      http.get('/api/v1/capabilities/', () => HttpResponse.json({}, { status: 500 })),
    )
    await refreshCapabilities()

    expect(result.current.capabilities.web_fetch).toBe(true)
  })
})
