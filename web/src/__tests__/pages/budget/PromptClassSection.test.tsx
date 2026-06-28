import { render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import type { getPromptClassBreakdown } from '@/api/endpoints/budget'
import type { PromptClassBreakdownRow } from '@/api/types/budget'
import { successFor } from '@/mocks/handlers/helpers'
import { PromptClassSection } from '@/pages/budget/PromptClassSection'
import { server } from '@/test-setup'

const ENDPOINT = '/api/v1/budget/prompt-class-breakdown'

function row(overrides: Partial<PromptClassBreakdownRow> = {}): PromptClassBreakdownRow {
  return {
    prompt_class_id: 'system:cos:chat',
    tier: 'medium',
    total_cost: 1.25,
    currency: 'USD',
    call_count: 10,
    input_tokens: 1000,
    output_tokens: 400,
    avg_latency_ms: 500,
    p95_latency_ms: 900,
    cache_hit_rate: 0.5,
    retry_rate: 0.1,
    success_rate: 0.9,
    ...overrides,
  }
}

function serveRows(rows: PromptClassBreakdownRow[]): void {
  server.use(
    http.get(ENDPOINT, () =>
      HttpResponse.json(successFor<typeof getPromptClassBreakdown>({ rows })),
    ),
  )
}

describe('PromptClassSection', () => {
  it('renders one row per prompt class, sorted by cost descending', async () => {
    serveRows([
      row({ prompt_class_id: 'system:memory:rerank', total_cost: 0.2 }),
      row({ prompt_class_id: 'system:research:synthesis', total_cost: 3.0 }),
    ])
    render(<PromptClassSection />)

    await screen.findByText('system:research:synthesis')
    const ids = screen
      .getAllByText(/^system:/)
      .map((el) => el.textContent)
    expect(ids).toEqual(['system:research:synthesis', 'system:memory:rerank'])
  })

  it('shows the empty state when no purpose has cost', async () => {
    serveRows([])
    render(<PromptClassSection />)
    await screen.findByText(/no prompt-purpose data yet/i)
  })

  it('shows an error banner when the fetch fails', async () => {
    server.use(
      http.get(ENDPOINT, () => new HttpResponse(null, { status: 500 })),
    )
    render(<PromptClassSection />)
    await screen.findByText(/prompt-purpose breakdown unavailable/i)
  })

  it('renders the skeleton while the fetch is in flight', async () => {
    serveRows([])
    const { container } = render(<PromptClassSection />)
    // loading starts true, so the skeleton is on screen before the fetch resolves.
    expect(container.querySelector('[data-skeleton-row]')).not.toBeNull()
    // Let the in-flight fetch settle so the test leaks no pending handle.
    await screen.findByText(/no prompt-purpose data yet/i)
  })

  it('renders a "--" placeholder for null latency and rate cells', async () => {
    serveRows([
      row({
        avg_latency_ms: null,
        p95_latency_ms: null,
        cache_hit_rate: null,
        success_rate: null,
      }),
    ])
    render(<PromptClassSection />)
    await screen.findByText('system:cos:chat')
    // retry_rate is always defined (0.0 over zero calls), so only the four
    // genuinely nullable latency/rate cells render the '--' placeholder.
    expect(screen.getAllByText('--')).toHaveLength(4)
  })
})
