import { useEffect } from 'react'

import type { LifecycleTransition } from '@/api/types/plans'
import { usePlanTransitionsStore } from '@/stores/planTransitions'

export interface UsePlanTransitionsReturn {
  transitions: readonly LifecycleTransition[]
  loading: boolean
  error: string | null
}

/**
 * Hydrate the durable record of how a plan reached its current status.
 * Delegates the fetch to the plan-transitions store (matching
 * usePlanForecast), and clears it on unmount so the next plan starts empty.
 */
export function usePlanTransitions(
  planId: string | undefined,
): UsePlanTransitionsReturn {
  const transitions = usePlanTransitionsStore((s) => s.transitions)
  const loading = usePlanTransitionsStore((s) => s.loading)
  const error = usePlanTransitionsStore((s) => s.error)

  useEffect(() => {
    if (planId === undefined) {
      usePlanTransitionsStore.getState().clear()
      return
    }
    void usePlanTransitionsStore.getState().fetchTransitions(planId)
    return () => {
      usePlanTransitionsStore.getState().clear()
    }
  }, [planId])

  return { transitions, loading, error }
}
