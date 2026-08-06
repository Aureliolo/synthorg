import { renderHook, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { server } from '@/test-setup'
import { usePlanEvaluation } from '@/hooks/usePlanEvaluation'
import { successFor } from '@/mocks/handlers/helpers'
import type { getPlanEvaluation } from '@/api/endpoints/plans'
import type { PlanEvaluationAttempt } from '@/api/types/plans'

function attemptsFor(planId: string): readonly PlanEvaluationAttempt[] {
  return [
    {
      attempt: 1,
      evaluated_at: '2026-08-01T10:00:00Z',
      objective_met: false,
      summary: `verdict for ${planId}`,
      verdicts: [],
    },
  ]
}

describe('usePlanEvaluation', () => {
  it('serves the attempts the backend returned for the plan', async () => {
    server.use(
      http.get('/api/v1/plans/:id/evaluation', ({ params }) =>
        HttpResponse.json(
          successFor<typeof getPlanEvaluation>({
            plan_id: String(params['id']),
            attempts: attemptsFor(String(params['id'])),
          }),
        ),
      ),
    )

    const { result } = renderHook(() => usePlanEvaluation('plan-a'))

    await waitFor(() => {
      expect(result.current.attempts).toHaveLength(1)
    })
    expect(result.current.attempts[0]?.summary).toBe('verdict for plan-a')
  })

  it('never shows plan A verdicts while pointed at plan B', async () => {
    // The store is repointed by an effect, which runs after the render that
    // changed planId. Without the plan-id association the first B render
    // reads A's attempts straight out of the store and paints them under
    // B's heading.
    server.use(
      http.get('/api/v1/plans/:id/evaluation', ({ params }) =>
        HttpResponse.json(
          successFor<typeof getPlanEvaluation>({
            plan_id: String(params['id']),
            attempts: attemptsFor(String(params['id'])),
          }),
        ),
      ),
    )

    const { result, rerender } = renderHook(
      ({ planId }: { planId: string }) => usePlanEvaluation(planId),
      { initialProps: { planId: 'plan-a' } },
    )
    await waitFor(() => {
      expect(result.current.attempts[0]?.summary).toBe('verdict for plan-a')
    })

    rerender({ planId: 'plan-b' })
    expect(result.current.attempts).toEqual([])

    await waitFor(() => {
      expect(result.current.attempts[0]?.summary).toBe('verdict for plan-b')
    })
  })

  it('holds nothing when there is no plan on screen', () => {
    const { result } = renderHook(() => usePlanEvaluation(null))

    expect(result.current.attempts).toEqual([])
    expect(result.current.loading).toBe(false)
    expect(result.current.error).toBeNull()
  })
})
