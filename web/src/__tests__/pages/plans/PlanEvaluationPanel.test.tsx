import { render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import type { PlanEvaluationAttempt } from '@/api/types/plans'
import { apiSuccess } from '@/mocks/handlers'
import { PlanEvaluationPanel } from '@/pages/plans/PlanEvaluationPanel'
import { server } from '@/test-setup'

function attempt(overrides?: Partial<PlanEvaluationAttempt>): PlanEvaluationAttempt {
  return {
    attempt: 1,
    summary: 'Read the workspace and played a full game.',
    verdicts: [
      {
        criterion: 'a person can play a full game',
        outcome: 'unmet',
        evidence: 'the board never renders',
      },
    ],
    objective_met: false,
    evaluated_at: '2026-07-01T10:00:00Z',
    ...overrides,
  }
}

function mockEvaluation(attempts: readonly PlanEvaluationAttempt[]): void {
  server.use(
    http.get('/api/v1/plans/:planId/evaluation', () =>
      HttpResponse.json(apiSuccess({ plan_id: 'plan-1', attempts })),
    ),
  )
}

describe('PlanEvaluationPanel', () => {
  it('renders nothing when nothing has judged the plan', async () => {
    mockEvaluation([])
    const { container } = render(<PlanEvaluationPanel planId="plan-1" />)
    await waitFor(() => {
      expect(container).toBeEmptyDOMElement()
    })
  })

  it('explains an unmet objective criterion by criterion', async () => {
    mockEvaluation([attempt()])
    render(<PlanEvaluationPanel planId="plan-1" />)

    expect(await screen.findByText('Not delivered')).toBeInTheDocument()
    expect(screen.getByText('a person can play a full game')).toBeInTheDocument()
    expect(screen.getByText('the board never renders')).toBeInTheDocument()
    expect(screen.getByText('Unmet')).toBeInTheDocument()
  })

  it('shows every judgement, the newest first', async () => {
    mockEvaluation([
      attempt({
        attempt: 2,
        objective_met: true,
        summary: 'The rebuilt front end plays the same game.',
        verdicts: [
          {
            criterion: 'a person can play a full game',
            outcome: 'met',
            evidence: 'played a full game to a top-out',
          },
        ],
        evaluated_at: '2026-07-02T10:00:00Z',
      }),
      attempt(),
    ])
    render(<PlanEvaluationPanel planId="plan-1" />)

    // The headline reads the latest judgement, not the first ever reached.
    expect(await screen.findByText('Delivered')).toBeInTheDocument()
    expect(screen.getByText(/Judgement 2/)).toBeInTheDocument()
    expect(screen.getByText(/Judgement 1/)).toBeInTheDocument()
  })

  it('surfaces a fetch error inline without blanking the panel', async () => {
    server.use(
      http.get('/api/v1/plans/:planId/evaluation', () =>
        HttpResponse.json({ error: 'boom' }, { status: 500 }),
      ),
    )
    render(<PlanEvaluationPanel planId="plan-1" />)
    await waitFor(() => {
      expect(screen.getByText(/Delivery verdict unavailable/)).toBeInTheDocument()
    })
  })
})
