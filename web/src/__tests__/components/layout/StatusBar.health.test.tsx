import { render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { StatusBar } from '@/components/layout/StatusBar'
import { server } from '@/test-setup'

/**
 * The top-bar health pill must resolve to a definite state after the first
 * readiness poll and never hang on "checking...". These tests exercise the
 * real usePolling loop (no mock) so the on-mount poll actually fires against
 * MSW, covering the failure modes that previously left the pill stuck.
 */
describe('StatusBar health pill resolution', () => {
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
    server.use(
      http.get('/api/v1/readyz', () =>
        HttpResponse.json({
          success: true,
          data: { status: 'ok', version: '0.0.0-test', uptime_seconds: 1 },
        }),
      ),
    )
    render(<StatusBar />)
    await waitFor(() =>
      expect(screen.queryByText('checking...')).not.toBeInTheDocument(),
    )
  })
})
