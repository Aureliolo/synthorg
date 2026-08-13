import { render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, it, expect } from 'vitest'
import { apiError, paginatedFor } from '@/mocks/handlers'
import { server } from '@/test-setup'
import type { listDispatchProfiles } from '@/api/endpoints/agents'
import type { DispatchProfile } from '@/api/types/agents'
import { DispatchComparisonSection } from '../DispatchComparisonSection'

const PROFILES = '/api/v1/agents/dispatch-profiles'

function profile(overrides: Partial<DispatchProfile> = {}): DispatchProfile {
  return {
    agent_id: 'agent-a',
    agent_name: 'Ada',
    role: 'Developer',
    department: 'Engineering',
    risk_tolerance: 'low',
    decision_making: 'analytical',
    creativity: 'low',
    provider_name: 'example-provider',
    model: 'example-capable-001',
    capability: 'capable',
    call_count: 40,
    outcome_counts: { success: 38, overloaded: 2 },
    latency: { p50_ms: 420, p90_ms: 980, p99_ms: 1400, max_ms: 1500 },
    last_call_at: '2026-08-13T11:00:00Z',
    min_calls: 20,
    has_enough_calls: true,
    success_rate_percent: 95,
    ...overrides,
  }
}

function page(rows: readonly DispatchProfile[]) {
  return paginatedFor<typeof listDispatchProfiles>({
    data: rows,
    limit: 50,
    nextCursor: null,
    hasMore: false,
    pagination: { limit: 50, next_cursor: null, has_more: false },
  })
}

describe('DispatchComparisonSection', () => {
  it('renders an agent with its rate, latencies and personality', async () => {
    server.use(http.get(PROFILES, () => HttpResponse.json(page([profile()]))))
    render(<DispatchComparisonSection />)

    expect(await screen.findByText('Ada')).toBeInTheDocument()
    expect(screen.getByText('95.0%')).toBeInTheDocument()
    expect(screen.getByText('420ms')).toBeInTheDocument()
    expect(screen.getByText('1.4s')).toBeInTheDocument()
    expect(screen.getByText(/low risk, analytical, low creativity/)).toBeInTheDocument()
  })

  it('groups agents that share a role and a bound pair', async () => {
    // The comparison the fixed-unit ruling exists to make answerable: two
    // agents on the same model differing only in temperament.
    server.use(
      http.get(PROFILES, () =>
        HttpResponse.json(
          page([
            profile(),
            profile({
              agent_id: 'agent-b',
              agent_name: 'Grace',
              risk_tolerance: 'high',
              creativity: 'high',
            }),
          ]),
        ),
      ),
    )
    render(<DispatchComparisonSection />)

    expect(
      await screen.findByText('Developer on example-provider / example-capable-001'),
    ).toBeInTheDocument()
    expect(screen.getByText('Ada')).toBeInTheDocument()
    expect(screen.getByText('Grace')).toBeInTheDocument()
  })

  it('refuses to show a rate the sample cannot support', async () => {
    server.use(
      http.get(PROFILES, () =>
        HttpResponse.json(
          page([
            profile({
              call_count: 4,
              has_enough_calls: false,
              success_rate_percent: 100,
            }),
          ]),
        ),
      ),
    )
    render(<DispatchComparisonSection />)

    expect(await screen.findByText('Not enough calls')).toBeInTheDocument()
    expect(screen.queryByText('100.0%')).not.toBeInTheDocument()
  })

  it('explains an empty roster instead of rendering a bare table', async () => {
    server.use(http.get(PROFILES, () => HttpResponse.json(page([]))))
    render(<DispatchComparisonSection />)

    expect(await screen.findByText('No active agents')).toBeInTheDocument()
  })

  it('surfaces a load failure with a retry', async () => {
    server.use(
      http.get(PROFILES, () => HttpResponse.json(apiError('boom'), { status: 500 })),
    )
    render(<DispatchComparisonSection />)

    expect(await screen.findByText('Could not load the comparison')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument()
  })
})
