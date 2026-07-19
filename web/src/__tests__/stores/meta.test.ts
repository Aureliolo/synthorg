import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { ErrorCategory, ErrorCode } from '@/api/types/errors'
import { useMetaStore } from '@/stores/meta'
import { useToastStore } from '@/stores/toast'
import { apiError, apiSuccess, pageEnvelope } from '@/mocks/handlers'
import { server } from '@/test-setup'

/** A SERVICE_UNAVAILABLE (503) body with a curated operator-facing detail. */
function serviceUnavailable(detail: string) {
  return apiError(detail, {
    error_code: ErrorCode.SERVICE_UNAVAILABLE,
    error_category: ErrorCategory.INTERNAL,
    detail,
  })
}

function resetStore() {
  useMetaStore.setState({
    config: null,
    proposals: [],
    alerts: [],
    abTests: [],
    evolutionSummary: null,
    evolutionAxes: [],
    signals: null,
    loading: false,
    error: null,
  })
  useToastStore.setState({ toasts: [] })
}

beforeEach(() => {
  resetStore()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('fetchProposals', () => {
  it('stores proposals and clears error on success', async () => {
    server.use(
      http.get('/api/v1/meta/proposals', () =>
        HttpResponse.json({
          success: true,
          data: [],
          error: null,
          error_detail: null,
          pagination: { limit: 50, next_cursor: null, has_more: false },
        }),
      ),
    )
    useMetaStore.setState({ error: 'stale' })

    await useMetaStore.getState().fetchProposals()

    expect(useMetaStore.getState().error).toBeNull()
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })

  it('sets error state on API failure without toasting (list-read pattern)', async () => {
    server.use(
      http.get('/api/v1/meta/proposals', () =>
        HttpResponse.json(apiError('boom')),
      ),
    )

    await useMetaStore.getState().fetchProposals()

    expect(useMetaStore.getState().error).toBe('boom')
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })
})

describe('fetchAlerts', () => {
  it('stores alerts and clears error on success', async () => {
    const alerts = [
      {
        id: 'alert-1',
        severity: 'warning',
        alert_type: 'inflection',
        description: 'Quality dropped sharply',
        affected_domains: ['performance'],
        signal_context: {},
        recommended_action: null,
        emitted_at: '2026-06-20T12:00:00Z',
      },
    ]
    server.use(
      http.get('/api/v1/meta/alerts', () => HttpResponse.json(pageEnvelope(alerts))),
    )
    useMetaStore.setState({ error: 'stale' })

    await useMetaStore.getState().fetchAlerts()

    expect(useMetaStore.getState().alerts).toEqual(alerts)
    expect(useMetaStore.getState().error).toBeNull()
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })

  it('sets error state on API failure without toasting (list-read pattern)', async () => {
    server.use(
      http.get('/api/v1/meta/alerts', () => HttpResponse.json(apiError('boom'))),
    )

    await useMetaStore.getState().fetchAlerts()

    expect(useMetaStore.getState().error).toBe('boom')
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })
})

describe('fetchSignals', () => {
  it('stores signals and clears error on success', async () => {
    const response = { enabled: true, domains: [] as unknown[] }
    server.use(
      http.get('/api/v1/meta/signals', () =>
        HttpResponse.json(apiSuccess(response)),
      ),
    )
    useMetaStore.setState({ error: 'stale' })

    await useMetaStore.getState().fetchSignals()

    expect(useMetaStore.getState().error).toBeNull()
    expect(useMetaStore.getState().signals).toEqual(response)
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })

  it('sets error state on API failure without toasting (list-read pattern)', async () => {
    server.use(
      http.get('/api/v1/meta/signals', () =>
        HttpResponse.json(apiError('boom')),
      ),
    )

    await useMetaStore.getState().fetchSignals()

    expect(useMetaStore.getState().error).toBe('boom')
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })

  it('surfaces the backend reason on a 503 (fail-closed, not a generic error)', async () => {
    server.use(
      http.get('/api/v1/meta/signals', () =>
        HttpResponse.json(
          serviceUnavailable('Signal reporting is not enabled.'),
          { status: 503 },
        ),
      ),
    )

    await useMetaStore.getState().fetchSignals()

    expect(useMetaStore.getState().error).toBe('Signal reporting is not enabled.')
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })
})
