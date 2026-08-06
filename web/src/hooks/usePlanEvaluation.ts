import { useEffect } from 'react'

import type { PlanEvaluationAttempt } from '@/api/types/plans'
import { usePlanEvaluationStore } from '@/stores/planEvaluation'

export interface UsePlanEvaluationReturn {
  attempts: readonly PlanEvaluationAttempt[]
  loading: boolean
  error: string | null
}

/**
 * Hydrate the evaluate stage's judgement history for a plan, newest attempt
 * first. Delegates the fetch to the plan-evaluation store (matching
 * usePlanForecast), and clears it on unmount / when there is no plan.
 */
export function usePlanEvaluation(planId: string | null): UsePlanEvaluationReturn {
  const storedPlanId = usePlanEvaluationStore((s) => s.planId)
  const attempts = usePlanEvaluationStore((s) => s.attempts)
  const loading = usePlanEvaluationStore((s) => s.loading)
  const error = usePlanEvaluationStore((s) => s.error)

  useEffect(() => {
    if (planId === null) {
      usePlanEvaluationStore.getState().clear()
      return
    }
    void usePlanEvaluationStore.getState().fetchEvaluation(planId)
    return () => {
      usePlanEvaluationStore.getState().clear()
    }
  }, [planId])

  // The effect that re-points the store runs after this render, so on the
  // first render after navigating A -> B the store still holds A's verdicts.
  // Withholding them until the ids agree is what stops A's text appearing
  // under B's heading; the request token only guards the late response.
  const matches = storedPlanId !== null && storedPlanId === planId
  if (!matches) {
    return { attempts: [], loading: planId !== null, error: null }
  }
  return { attempts, loading, error }
}
