import type { StoreApi } from 'zustand'
import { create } from 'zustand'

import { getPlanTransitions } from '@/api/endpoints/plans'
import type { LifecycleTransition } from '@/api/types/plans'
import { createLogger } from '@/lib/logger'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'

const log = createLogger('plan-transitions')

// Monotonic request token: navigating between plans can leave an older fetch
// in flight, and its late resolve must not paint one plan's history under
// another's heading.
let requestToken = 0

export interface PlanTransitionsState {
  /** The plan the rows and error below belong to, `null` when cleared. */
  planId: string | null
  transitions: readonly LifecycleTransition[]
  loading: boolean
  error: string | null
  fetchTransitions: (planId: string) => Promise<void>
  clear: () => void
}

type PtSet = StoreApi<PlanTransitionsState>['setState']

async function fetchTransitionsImpl(set: PtSet, planId: string): Promise<void> {
  const token = (requestToken += 1)
  set({ planId, loading: true, error: null, transitions: [] })
  try {
    const transitions = await getPlanTransitions(planId)
    if (token !== requestToken) return
    set({ transitions, loading: false })
  } catch (err) {
    if (token !== requestToken) return
    const message = getErrorMessage(err)
    log.warn('Fetch plan transitions failed', sanitizeForLog(err))
    set({ loading: false, error: message })
  }
}

/**
 * How the plan on screen reached its current status: the durable ledger rows,
 * not a status field's current value. A per-view store like the evaluation
 * one, and a pure API consumer (re-hydrated on mount, nothing persisted).
 */
export const usePlanTransitionsStore = create<PlanTransitionsState>((set) => ({
  planId: null,
  transitions: [],
  loading: false,
  error: null,
  fetchTransitions: (planId) => fetchTransitionsImpl(set, planId),
  clear: () => {
    requestToken += 1
    set({ planId: null, transitions: [], loading: false, error: null })
  },
}))
