import { render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, it, expect } from 'vitest'
import { apiError, apiSuccess } from '@/mocks/handlers'
import { server } from '@/test-setup'
import type { ModelServiceability } from '@/api/types/providers'
import { ProviderServiceabilitySection } from '../ProviderServiceabilitySection'

const SCOPED = '/api/v1/providers/:name/serviceability'
const FLEET = '/api/v1/providers/serviceability'

function row(overrides: Partial<ModelServiceability> = {}): ModelServiceability {
  return {
    provider_name: 'test-provider',
    model: 'example-capable-001',
    window_seconds: 900,
    call_count: 12,
    outcome_counts: { success: 11, rate_limit: 1 },
    latency: { p50_ms: 420, p90_ms: 980, p99_ms: 1400, max_ms: 1500 },
    last_call_timestamp: '2026-01-01T00:00:00Z',
    first_failure_timestamp: null,
    degraded_error_rate_percent: 10,
    down_error_rate_percent: 50,
    min_calls_for_verdict: 3,
    error_rate_percent: 8.33,
    latched_failure: null,
    latched_since: null,
    has_latching_failure: false,
    verdict: 'up',
    ...overrides,
  }
}

describe('ProviderServiceabilitySection', () => {
  it('renders a served model with its verdict, calls and percentiles', async () => {
    render(<ProviderServiceabilitySection providerName="test-provider" />)

    expect(await screen.findByText('example-capable-001')).toBeInTheDocument()
    expect(screen.getByText('12')).toBeInTheDocument()
    expect(screen.getByText('1 throttled')).toBeInTheDocument()
    // p50 and p99 are separate columns precisely so a slow tail is visible.
    expect(screen.getByText('420ms')).toBeInTheDocument()
    expect(screen.getByText('1.4s')).toBeInTheDocument()
  })

  it('names the failure classes that make a model unserviceable', async () => {
    server.use(
      http.get(SCOPED, () =>
        HttpResponse.json(
          apiSuccess([
            row({
              outcome_counts: { internal: 6, overloaded: 3, payment_required: 1 },
              verdict: 'down',
              has_latching_failure: true,
            }),
          ]),
        ),
      ),
    )
    render(<ProviderServiceabilitySection providerName="test-provider" />)

    expect(await screen.findByText(/1 balance empty/)).toBeInTheDocument()
    expect(screen.getByText(/6 server error/)).toBeInTheDocument()
    expect(screen.getByText(/3 overloaded/)).toBeInTheDocument()
    expect(screen.getByLabelText('Down')).toBeInTheDocument()
  })

  it('shows the connection name only in the fleet-wide view', async () => {
    const { unmount } = render(
      <ProviderServiceabilitySection providerName="test-provider" />,
    )
    await screen.findByText('example-capable-001')
    expect(screen.queryByText('test-provider')).not.toBeInTheDocument()
    unmount()

    render(<ProviderServiceabilitySection />)
    expect(await screen.findByText('test-provider')).toBeInTheDocument()
  })

  it('labels a record carrying no model rather than rendering a blank cell', async () => {
    server.use(
      http.get(SCOPED, () =>
        HttpResponse.json(apiSuccess([row({ model: null, verdict: 'unknown' })])),
      ),
    )
    render(<ProviderServiceabilitySection providerName="test-provider" />)

    expect(await screen.findByText('connection only')).toBeInTheDocument()
  })

  it('explains an empty window instead of rendering a bare table', async () => {
    server.use(http.get(FLEET, () => HttpResponse.json(apiSuccess([]))))
    render(<ProviderServiceabilitySection />)

    expect(await screen.findByText('No calls in the window')).toBeInTheDocument()
  })

  it('surfaces a load failure with a retry', async () => {
    server.use(
      http.get(SCOPED, () => HttpResponse.json(apiError('boom'), { status: 500 })),
    )
    render(<ProviderServiceabilitySection providerName="test-provider" />)

    expect(
      await screen.findByText('Could not load serviceability'),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument()
  })
})
