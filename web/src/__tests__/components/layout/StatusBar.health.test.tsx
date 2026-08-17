import { render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { useAnalyticsStore } from '@/stores/analytics'
import { useWebSocketStore } from '@/stores/websocket'
import { StatusBar } from '@/components/layout/StatusBar'
import { apiError, successFor } from '@/mocks/handlers'
import { server } from '@/test-setup'
import type { getHealthDetail } from '@/api/endpoints/health'
import type { HealthStatus, MemoryHealth } from '@/api/types/system'

/**
 * The top-bar health pill must resolve to a definite state after the first
 * probe and never hang on "checking...". These tests exercise the real
 * usePolling loop (no mock) so the on-mount poll actually fires against MSW.
 *
 * The pill reads ``/health``, the same per-subsystem snapshot the dialog it
 * opens renders, so the two cannot report different verdicts. Readiness cannot
 * serve this surface: it is binary, carries no component topology, and a
 * subsystem may abstain from its verdict on purpose.
 */

const DURABLE_MEMORY: MemoryHealth = {
  state: 'durable',
  backend: 'sqlvector',
  detail: null,
}

function resetStore() {
  useAnalyticsStore.setState({
    overview: null,
    forecast: null,
    activities: [],
    budgetConfig: null,
    loading: false,
    error: null,
  })
}

function healthBody(overrides: Partial<HealthStatus> = {}) {
  return successFor<typeof getHealthDetail>({
    status: 'ok',
    persistence: true,
    message_bus: true,
    providers: 'ok',
    telemetry: 'disabled',
    memory: DURABLE_MEMORY,
    backup: { state: 'wired', detail: null },
    cost_recording: { state: 'ok', dropped_records: 0, detail: null },
    version: '0.0.0-test',
    uptime_seconds: 1,
    ...overrides,
  })
}

function healthOk() {
  return HttpResponse.json(healthBody())
}

describe('StatusBar health pill resolution', () => {
  beforeEach(() => {
    // Reset the shared analytics store and block the dashboard-data fetches
    // StatusBar fires on mount so one test's store/data cannot leak into the
    // next (each test owns only its /health handler). Mirrors StatusBar.test.
    resetStore()
    // Reset the WS store too: the combined pill folds WS state into its label,
    // so a leaked "connected" from another test would change what resolves.
    useWebSocketStore.setState({ connected: false, reconnectExhausted: false })
    server.use(
      http.get('/api/v1/analytics/overview', () =>
        HttpResponse.json(apiError('blocked for StatusBar health test')),
      ),
      http.get('/api/v1/analytics/forecast', () =>
        HttpResponse.json(apiError('blocked for StatusBar health test')),
      ),
      http.get('/api/v1/budget/config', () =>
        HttpResponse.json(apiError('blocked for StatusBar health test')),
      ),
      http.get('/api/v1/activities', () =>
        HttpResponse.json(apiError('blocked for StatusBar health test')),
      ),
      http.get('/api/v1/departments', () =>
        HttpResponse.json(apiError('blocked for StatusBar health test')),
      ),
    )
  })

  it('resolves to "system down" on a 503 unavailable verdict', async () => {
    // A 503 from /health still carries the full breakdown, which is precisely
    // when an operator needs it, so the aggregate verdict in the body is what
    // decides rather than the transport status.
    server.use(
      http.get('/api/v1/health', () =>
        HttpResponse.json(healthBody({ status: 'unavailable', persistence: false }), {
          status: 503,
        }),
      ),
    )
    render(<StatusBar />)
    await waitFor(() =>
      expect(screen.getByText('system down')).toBeInTheDocument(),
    )
    expect(screen.queryByText('checking...')).not.toBeInTheDocument()
  })

  it('resolves to "system down" on a transport failure, never stuck on checking', async () => {
    server.use(http.get('/api/v1/health', () => HttpResponse.error()))
    render(<StatusBar />)
    await waitFor(() =>
      expect(screen.getByText('system down')).toBeInTheDocument(),
    )
    expect(screen.queryByText('checking...')).not.toBeInTheDocument()
  })

  it('resolves to healthy once a probe succeeds', async () => {
    // WS connected so the combined pill can reach the fully-healthy label
    // rather than "reconnecting"; the probe drives the HTTP half to ok.
    useWebSocketStore.setState({ connected: true })
    server.use(http.get('/api/v1/health', healthOk))
    render(<StatusBar />)
    await waitFor(() =>
      expect(screen.getByText('all systems normal')).toBeInTheDocument(),
    )
    expect(screen.queryByText('checking...')).not.toBeInTheDocument()
  })

  it('recovers from "system down" to healthy on the next successful poll', async () => {
    server.use(http.get('/api/v1/health', () => HttpResponse.error()))
    render(<StatusBar />)
    await waitFor(() =>
      expect(screen.getByText('system down')).toBeInTheDocument(),
    )

    // The next poll succeeds; a visibilitychange re-arms an immediate tick so
    // the recovery is driven without waiting the full poll interval.
    server.use(http.get('/api/v1/health', healthOk))
    document.dispatchEvent(new Event('visibilitychange'))
    await waitFor(() =>
      expect(screen.queryByText('system down')).not.toBeInTheDocument(),
    )
  })

  it('reports a subsystem the readiness gate abstains on', async () => {
    // Memory not wired abstains from the readiness verdict on purpose, so
    // /readyz answers 200. The pill must still surface it, or it reads "all
    // systems normal" beside a dialog reporting memory is not running.
    useWebSocketStore.setState({ connected: true })
    server.use(
      http.get('/api/v1/health', () =>
        HttpResponse.json(
          healthBody({
            status: 'ok',
            memory: {
              state: 'off',
              backend: 'sqlvector',
              detail: 'No embedding model resolved.',
            },
          }),
        ),
      ),
    )
    render(<StatusBar />)
    await waitFor(() =>
      expect(screen.getByText('system degraded')).toBeInTheDocument(),
    )
    expect(screen.queryByText('all systems normal')).not.toBeInTheDocument()
  })
})
