import { render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { useAnalyticsStore } from '@/stores/analytics'
import { StatusBar } from '@/components/layout/StatusBar'
import { apiError } from '@/mocks/handlers'
import { server } from '@/test-setup'

/**
 * The top-bar health pill must resolve to a definite state after the first
 * readiness poll and never hang on "checking...". These tests exercise the
 * real usePolling loop (no mock) so the on-mount poll actually fires against
 * MSW, covering the failure modes that previously left the pill stuck.
 */

function resetStore() {
  useAnalyticsStore.setState({
    overview: null,
    forecast: null,
    departmentHealths: [],
    activities: [],
    budgetConfig: null,
    orgHealthPercent: null,
    loading: false,
    error: null,
  })
}

function readyzOk() {
  return HttpResponse.json({
    success: true,
    data: { status: 'ok', version: '0.0.0-test', uptime_seconds: 1 },
  })
}

describe('StatusBar health pill resolution', () => {
  beforeEach(() => {
    // Reset the shared analytics store and block the dashboard-data fetches
    // StatusBar fires on mount so one test's store/data cannot leak into the
    // next (each test owns only its /readyz handler). Mirrors StatusBar.test.
    resetStore()
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

  it('resolves to "system down" on a 503 readiness verdict', async () => {
    server.use(
      http.get('/api/v1/readyz', () =>
        HttpResponse.json({ success: false }, { status: 503 }),
      ),
    )
    render(<StatusBar />)
    await waitFor(() =>
      expect(screen.getByText('system down')).toBeInTheDocument(),
    )
    expect(screen.queryByText('checking...')).not.toBeInTheDocument()
  })

  it('resolves to "system down" on a transport failure, never stuck on checking', async () => {
    server.use(http.get('/api/v1/readyz', () => HttpResponse.error()))
    render(<StatusBar />)
    await waitFor(() =>
      expect(screen.getByText('system down')).toBeInTheDocument(),
    )
    expect(screen.queryByText('checking...')).not.toBeInTheDocument()
  })

  it('leaves "checking..." once a readiness probe succeeds', async () => {
    server.use(http.get('/api/v1/readyz', readyzOk))
    render(<StatusBar />)
    await waitFor(() =>
      expect(screen.queryByText('checking...')).not.toBeInTheDocument(),
    )
  })

  it('recovers from "system down" to healthy on the next successful poll', async () => {
    server.use(http.get('/api/v1/readyz', () => HttpResponse.error()))
    render(<StatusBar />)
    await waitFor(() =>
      expect(screen.getByText('system down')).toBeInTheDocument(),
    )

    // The next poll succeeds; a visibilitychange re-arms an immediate tick so
    // the recovery is driven without waiting the full poll interval.
    server.use(http.get('/api/v1/readyz', readyzOk))
    document.dispatchEvent(new Event('visibilitychange'))
    await waitFor(() =>
      expect(screen.queryByText('system down')).not.toBeInTheDocument(),
    )
  })
})
