import { render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import type { ForecastView } from '@/api/types/budget'
import { apiSuccess } from '@/mocks/handlers'
import { PlanForecastPanel } from '@/pages/plans/PlanForecastPanel'
import { server } from '@/test-setup'

function forecast(overrides?: Partial<ForecastView>): ForecastView {
  return {
    forecast_id: 'fc-1',
    brief_hash: 'a'.repeat(64),
    estimated_cost: 4.5,
    lower_bound: 3,
    upper_bound: 6,
    currency: 'USD',
    decision: 'pending',
    decided_at: null,
    decided_by: null,
    ceiling_amount: null,
    halt_context: null,
    created_at: '2026-06-01T10:00:00Z',
    updated_at: '2026-06-01T10:00:00Z',
    ...overrides,
  }
}

function mockForecast(value: ForecastView): void {
  server.use(
    http.get('/api/v1/budget/forecasts/:forecastId', () =>
      HttpResponse.json(apiSuccess(value)),
    ),
  )
}

describe('PlanForecastPanel', () => {
  it('renders nothing when the plan carries no forecast', () => {
    const { container } = render(<PlanForecastPanel forecastId={null} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('surfaces the estimate, its band, and the decision state', async () => {
    mockForecast(forecast())
    const { container } = render(<PlanForecastPanel forecastId="fc-1" />)
    // Await the resolved body (the decision pill), not just the title, so the
    // fetch has landed before reading the rendered figures.
    expect(await screen.findByText('Awaiting decision')).toBeInTheDocument()
    // Currency symbol varies by locale (USD under en-GB is "US$"); assert the
    // numbers, not the glyph.
    const text = container.textContent
    expect(text).toContain('4.50')
    expect(text).toContain('3.00')
    expect(text).toContain('6.00')
  })

  it('flags a hard-ceiling halt', async () => {
    mockForecast(
      forecast({
        halt_context: {
          accumulated_cost: 8,
          ceiling_amount: 7,
          currency: 'USD',
          halted_at: '2026-06-02T10:00:00Z',
        },
      }),
    )
    render(<PlanForecastPanel forecastId="fc-1" />)
    expect(await screen.findByText(/Run halted/)).toBeInTheDocument()
  })

  it('surfaces a fetch error inline without blanking the panel', async () => {
    server.use(
      http.get('/api/v1/budget/forecasts/:forecastId', () =>
        HttpResponse.json({ error: 'boom' }, { status: 500 }),
      ),
    )
    render(<PlanForecastPanel forecastId="fc-1" />)
    await waitFor(() =>
      expect(screen.getByText(/Cost forecast unavailable/)).toBeInTheDocument(),
    )
  })
})
