import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '@/test-setup'
import { useProvidersStore } from '@/stores/providers'

/**
 * Provider health has to be re-derivable from the dashboard.
 *
 * The gap these cover: a successful connection test told the operator the
 * provider answered while the badge beside it still showed the aggregate from
 * before that test, because nothing re-read health afterwards. Re-saving the
 * provider was the only control that moved it.
 */

const INITIAL = useProvidersStore.getState()

beforeEach(() => {
  useProvidersStore.setState(INITIAL, true)
})

afterEach(() => {
  vi.restoreAllMocks()
  useProvidersStore.setState(INITIAL, true)
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

  it('adopts the summary the recheck returns', async () => {
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

    expect(useProvidersStore.getState().recheckingHealth).toBe(false)
  })

  it('keeps the page usable when a recheck fails', async () => {
    // The recorded health is still the best answer available, so a failed
    // recheck must not blank the badge or wedge the spinner on.
    server.use(
      http.post('/api/v1/providers/:name/health/recheck', () =>
        HttpResponse.json({ success: false }, { status: 500 }),
      ),
    )

    await useProvidersStore.getState().recheckProviderHealth('test-provider')

    expect(useProvidersStore.getState().recheckingHealth).toBe(false)
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
    expect(useProvidersStore.getState().recheckingHealth).toBe(false)
  })

  it('keeps the recorded map when a recheck-all fails', async () => {
    server.use(
      http.post('/api/v1/providers/health/recheck', () =>
        HttpResponse.json({ success: false }, { status: 500 }),
      ),
    )

    await useProvidersStore.getState().recheckAllHealth()

    expect(useProvidersStore.getState().recheckingHealth).toBe(false)
  })
})
