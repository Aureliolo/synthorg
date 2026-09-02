import { render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import type { getCallAnalytics } from '@/api/endpoints/budget'
import type { AnalyticsAggregation } from '@/api/types/budget'
import { successFor } from '@/mocks/handlers/helpers'
import { CallAnalyticsSection } from '@/pages/budget/CallAnalyticsSection'
import { server } from '@/test-setup'

const ENDPOINT = '/api/v1/budget/call-analytics'

function aggregation(overrides: Partial<AnalyticsAggregation> = {}): AnalyticsAggregation {
  return {
    total_calls: 12,
    success_count: 10,
    failure_count: 2,
    unreported_count: 0,
    success_rate: 0.8333,
    retry_count: 1,
    retry_rate: 0.0833,
    input_tokens: 10000,
    cached_input_tokens: 4000,
    cached_input_share: 0.4,
    avg_latency_ms: 512,
    p95_latency_ms: 980,
    by_finish_reason: [['stop', 12]],
    orchestration_ratio: {
      alert_level: 'normal',
      coordination_tokens: 100,
      productive_tokens: 900,
      ratio: 0.1,
      system_tokens: 0,
      total_tokens: 1000,
    },
    ...overrides,
  }
}

function serve(payload: AnalyticsAggregation): void {
  server.use(
    http.get(ENDPOINT, () => HttpResponse.json(successFor<typeof getCallAnalytics>(payload))),
  )
}

describe('CallAnalyticsSection', () => {
  it('renders the cached input share as a whole percentage', async () => {
    serve(aggregation({ cached_input_share: 0.4 }))
    render(<CallAnalyticsSection />)

    await screen.findByText('Cached input share')
    expect(screen.getByText('40%')).toBeInTheDocument()
  })

  it('renders an absence for a share of no input tokens', async () => {
    serve(aggregation({ input_tokens: 0, cached_input_tokens: 0, cached_input_share: null }))
    render(<CallAnalyticsSection />)

    await screen.findByText('Cached input share')
    // The share is null while the latency figures are present, so exactly
    // one card renders the placeholder rather than a claimed 0%.
    expect(screen.getAllByText('--')).toHaveLength(1)
  })

  it('shows the empty state when no call has been recorded', async () => {
    serve(aggregation({ total_calls: 0 }))
    render(<CallAnalyticsSection />)
    await screen.findByText(/no call analytics yet/i)
  })

  it('shows an error banner when the fetch fails', async () => {
    server.use(http.get(ENDPOINT, () => new HttpResponse(null, { status: 500 })))
    render(<CallAnalyticsSection />)
    await screen.findByText(/call analytics unavailable/i)
  })

  it('renders one skeleton per metric while the fetch is in flight', async () => {
    serve(aggregation({ total_calls: 0 }))
    render(<CallAnalyticsSection />)
    expect(screen.getAllByTestId('skeleton-value')).toHaveLength(8)
    await screen.findByText(/no call analytics yet/i)
  })
})
