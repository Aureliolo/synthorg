import type { StoreApi } from 'zustand'
import { create } from 'zustand'

import { getPlanEvaluation } from '@/api/endpoints/plans'
import type { PlanEvaluationAttempt } from '@/api/types/plans'
import { createLogger } from '@/lib/logger'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'

const log = createLogger('plan-evaluation')

// Monotonic request token: a plan-navigation change can leave an older fetch
// in flight, and its late resolve must not clobber the current plan's verdicts.
let requestToken = 0

export interface PlanEvaluationState {
  /** The plan the attempts and error below belong to, `null` when cleared. */
  planId: string | null
  attempts: readonly PlanEvaluationAttempt[]
  loading: boolean
  error: string | null
  fetchEvaluation: (planId: string) => Promise<void>
  clear: () => void
}

type PeSet = StoreApi<PlanEvaluationState>['setState']

async function fetchEvaluationImpl(set: PeSet, planId: string): Promise<void> {
  const token = (requestToken += 1)
  // The plan id is stamped here, before the request goes out: navigating from
  // plan A to plan B renders once before the effect runs, and a consumer that
  // could not tell whose verdicts it holds would paint A's under B's heading.
  set({ planId, loading: true, error: null, attempts: [] })
  try {
    const evaluation = await getPlanEvaluation(planId)
    if (token !== requestToken) return
    set({ attempts: evaluation.attempts, loading: false })
  } catch (err) {
    if (token !== requestToken) return
    const message = getErrorMessage(err)
    log.warn('Fetch plan evaluation failed', sanitizeForLog(err))
    set({ loading: false, error: message })
  }
}

/**
 * The evaluate stage's judgement history for the plan on screen. A dedicated
 * per-view store so navigating between plans never shows another plan's
 * verdicts; still a pure API consumer (re-hydrated on mount, nothing persisted).
 */
export const usePlanEvaluationStore = create<PlanEvaluationState>((set) => ({
  planId: null,
  attempts: [],
  loading: false,
  error: null,
  fetchEvaluation: (planId) => fetchEvaluationImpl(set, planId),
  clear: () => {
    requestToken += 1
    set({ planId: null, attempts: [], loading: false, error: null })
  },
}))
