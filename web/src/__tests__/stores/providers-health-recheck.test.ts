import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '@/test-setup'
import { useProvidersStore } from '@/stores/providers'
import { resetHealthRevision } from '@/stores/providers/health-revision'

/**
 * Provider health has to be re-derivable from the dashboard.
 *
 * The gap these cover: a successful connection test told the operator the
 * provider answered while the badge beside it still showed the aggregate from
 * before that test, because nothing re-read health afterwards. Re-saving the
 * provider was the only control that moved it.
 */

const INITIAL = useProvidersStore.getState()

/** A health row with every required field, varied per case by spread. */
const HEALTH_ROW = {
  last_check_timestamp: null,
  avg_response_time_ms: 9,
  error_rate_percent_24h: 0,
  calls_last_24h: 1,
  health_status: 'up',
  total_tokens_24h: 0,
  total_cost_24h: 0,
} as const

beforeEach(() => {
  useProvidersStore.setState(INITIAL, true)
  resetHealthRevision()
})

afterEach(() => {
  vi.restoreAllMocks()
  useProvidersStore.setState(INITIAL, true)
  resetHealthRevision()
})

describe('provider health recheck', () => {
  it('re-reads health after a connection test', async () => {
    // The server records the test's verdict against health, so the badge is
    // stale the moment the test returns unless this refetch happens.
    useProvidersStore.setState({
      selectedProvider: { name: 'test-provider' } as never,
    })
    let healthReads = 0
    server.use(
      http.get('/api/v1/providers/:name/health', () => {
        healthReads += 1
        return HttpResponse.json({
          success: true,
          data: {
            last_check_timestamp: null,
            avg_response_time_ms: null,
            error_rate_percent_24h: 0,
            calls_last_24h: 0,
            health_status: 'up',
            total_tokens_24h: 0,
            total_cost_24h: 0,
          },
          error: null,
          error_detail: null,
        })
      }),
    )

    await useProvidersStore.getState().testConnection('test-provider')

    expect(healthReads).toBeGreaterThan(0)
  })

  it('calls the provider and adopts the health that call produced', async () => {
    useProvidersStore.setState({
      selectedProvider: { name: 'test-provider' } as never,
      selectedProviderHealth: { health_status: 'down' } as never,
    })
    let rechecks = 0
    const healthy = {
      last_check_timestamp: null,
      avg_response_time_ms: 12,
      error_rate_percent_24h: 0,
      calls_last_24h: 1,
      health_status: 'up',
      total_tokens_24h: 0,
      total_cost_24h: 0,
    }
    server.use(
      http.post('/api/v1/providers/:name/health/recheck', () => {
        rechecks += 1
        return HttpResponse.json({
          success: true,
          data: healthy,
          error: null,
          error_detail: null,
        })
      }),
      // The trailing detail refetch re-reads this, so it has to agree with
      // what the recheck just recorded or the badge would flap back.
      http.get('/api/v1/providers/:name/health', () =>
        HttpResponse.json({
          success: true,
          data: healthy,
          error: null,
          error_detail: null,
        }),
      ),
    )

    await useProvidersStore.getState().recheckProviderHealth('test-provider')

    expect(rechecks).toBe(1)
    const health = useProvidersStore.getState().selectedProviderHealth
    expect(health?.health_status).toBe('up')
    expect(health?.calls_last_24h).toBe(1)
    expect(useProvidersStore.getState().recheckingHealth).toBe(false)
  })

  it('leaves a readable badge and no stuck spinner when a recheck fails', async () => {
    // The server's recorded health is still the best answer available, so a
    // failed recheck falls back to re-reading it rather than blanking.
    useProvidersStore.setState({
      selectedProvider: { name: 'test-provider' } as never,
      selectedProviderHealth: { health_status: 'degraded' } as never,
    })
    server.use(
      http.post('/api/v1/providers/:name/health/recheck', () =>
        HttpResponse.json({ success: false }, { status: 500 }),
      ),
    )

    await useProvidersStore.getState().recheckProviderHealth('test-provider')

    expect(useProvidersStore.getState().selectedProviderHealth).not.toBeNull()
    expect(useProvidersStore.getState().recheckingHealth).toBe(false)
  })

  it('ignores a recheck that resolves after the operator moved on', async () => {
    // The trailing refetch would otherwise overwrite the provider now on
    // screen with the one whose recheck happened to finish later.
    useProvidersStore.setState({
      selectedProvider: { name: 'other-provider' } as never,
      selectedProviderHealth: { health_status: 'degraded' } as never,
    })
    server.use(
      http.post('/api/v1/providers/:name/health/recheck', () =>
        HttpResponse.json({
          success: true,
          data: {
            last_check_timestamp: null,
            avg_response_time_ms: 12,
            error_rate_percent_24h: 0,
            calls_last_24h: 1,
            health_status: 'up',
            total_tokens_24h: 0,
            total_cost_24h: 0,
          },
          error: null,
          error_detail: null,
        }),
      ),
    )

    await useProvidersStore.getState().recheckProviderHealth('test-provider')

    expect(useProvidersStore.getState().selectedProvider?.name).toBe('other-provider')
    expect(useProvidersStore.getState().selectedProviderHealth?.health_status).toBe(
      'degraded',
    )
  })

  it('replaces the whole health map on a recheck-all', async () => {
    server.use(
      http.post('/api/v1/providers/health/recheck', () =>
        HttpResponse.json({
          success: true,
          data: {
            'test-provider': {
              last_check_timestamp: null,
              avg_response_time_ms: 9,
              error_rate_percent_24h: 0,
              calls_last_24h: 1,
              health_status: 'up',
              total_tokens_24h: 0,
              total_cost_24h: 0,
            },
          },
          error: null,
          error_detail: null,
        }),
      ),
    )

    await useProvidersStore.getState().recheckAllHealth()

    expect(useProvidersStore.getState().healthMap['test-provider']?.health_status).toBe('up')
    expect(useProvidersStore.getState().recheckingAllHealth).toBe(false)
  })

  it('refreshes the open detail badge from a sweep that covered it', async () => {
    // The detail page reads selectedProviderHealth, not the list's map, so a
    // sweep that covered it has to say so there too.
    useProvidersStore.setState({
      selectedProvider: { name: 'test-provider' } as never,
      selectedProviderHealth: { health_status: 'down' } as never,
    })
    server.use(
      http.post('/api/v1/providers/health/recheck', () =>
        HttpResponse.json({
          success: true,
          data: {
            'test-provider': {
              last_check_timestamp: null,
              avg_response_time_ms: 9,
              error_rate_percent_24h: 0,
              calls_last_24h: 1,
              health_status: 'up',
              total_tokens_24h: 0,
              total_cost_24h: 0,
            },
          },
          error: null,
          error_detail: null,
        }),
      ),
    )

    await useProvidersStore.getState().recheckAllHealth()

    expect(useProvidersStore.getState().selectedProviderHealth?.health_status).toBe('up')
  })

  it('keeps a sweep verdict a slower detail read would have overwritten', async () => {
    // An individual recheck fires a trailing detail read. A sweep that lands
    // while that read is in flight holds the newer verdict, so the read must
    // not resolve last and restore the one the sweep replaced.
    useProvidersStore.setState({
      selectedProvider: { name: 'test-provider' } as never,
      selectedProviderHealth: { health_status: 'down' } as never,
    })
    let releaseDetailHealth = (): void => {}
    const detailHealthHeld = new Promise<void>((resolve) => {
      releaseDetailHealth = resolve
    })
    server.use(
      // The detail read's health leg, held open until the sweep has applied.
      http.get('/api/v1/providers/:name/health', async () => {
        await detailHealthHeld
        return HttpResponse.json({
          success: true,
          data: { ...HEALTH_ROW, health_status: 'down' },
          error: null,
          error_detail: null,
        })
      }),
      http.post('/api/v1/providers/:name/health/recheck', () =>
        HttpResponse.json({
          success: true,
          data: { ...HEALTH_ROW, health_status: 'degraded' },
          error: null,
          error_detail: null,
        }),
      ),
      http.post('/api/v1/providers/health/recheck', () =>
        HttpResponse.json({
          success: true,
          data: { 'test-provider': { ...HEALTH_ROW, health_status: 'up' } },
          error: null,
          error_detail: null,
        }),
      ),
    )

    const trailing = useProvidersStore
      .getState()
      .recheckProviderHealth('test-provider')
    await useProvidersStore.getState().recheckAllHealth()
    releaseDetailHealth()
    await trailing

    expect(useProvidersStore.getState().selectedProviderHealth?.health_status).toBe('up')
  })

  it('keeps the recorded map when a recheck-all fails', async () => {
    useProvidersStore.setState({
      healthMap: { 'test-provider': { health_status: 'degraded' } as never },
    })
    server.use(
      http.post('/api/v1/providers/health/recheck', () =>
        HttpResponse.json({ success: false }, { status: 500 }),
      ),
    )

    await useProvidersStore.getState().recheckAllHealth()

    expect(useProvidersStore.getState().healthMap['test-provider']?.health_status).toBe(
      'degraded',
    )
    expect(useProvidersStore.getState().recheckingAllHealth).toBe(false)
  })

  it('keeps the two recheck flags independent', async () => {
    // Both surfaces are reachable at once, so one flag would let whichever
    // finished first re-enable the other's button mid-request.
    expect(useProvidersStore.getState().recheckingHealth).toBe(false)
    expect(useProvidersStore.getState().recheckingAllHealth).toBe(false)

    const sweep = useProvidersStore.getState().recheckAllHealth()
    expect(useProvidersStore.getState().recheckingAllHealth).toBe(true)
    expect(useProvidersStore.getState().recheckingHealth).toBe(false)

    await sweep
  })
})
