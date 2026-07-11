import { listPlans } from '@/api/endpoints/plans'
import type { PlanStatus } from '@/api/types'
import { getErrorMessage } from '@/utils/errors'

import { bumpDetailRequestToken, isStaleListRequest, nextListRequestToken } from './_state'
import type { PlansGet, PlansSet } from './types'

const PLANS_PAGE_LIMIT = 200

async function fetchPlansImpl(set: PlansSet): Promise<void> {
  const token = nextListRequestToken()
  set({ listLoading: true, listError: null, nextCursor: null, hasMore: false })
  try {
    const result = await listPlans({ limit: PLANS_PAGE_LIMIT })
    if (isStaleListRequest(token)) return
    set({
      plans: result.data,
      nextCursor: result.nextCursor,
      hasMore: result.hasMore,
    })
  } catch (err) {
    if (isStaleListRequest(token)) return
    set({ listError: getErrorMessage(err) })
  } finally {
    if (!isStaleListRequest(token)) set({ listLoading: false })
  }
}

async function fetchMorePlansImpl(set: PlansSet, get: PlansGet): Promise<void> {
  const { listLoading, hasMore, nextCursor } = get()
  if (listLoading || !hasMore || !nextCursor) return
  const token = nextListRequestToken()
  set({ listLoading: true, listError: null })
  try {
    const result = await listPlans({ cursor: nextCursor, limit: PLANS_PAGE_LIMIT })
    if (isStaleListRequest(token)) return
    set((s) => {
      const existingIds = new Set(s.plans.map((p) => p.id))
      const deduped = result.data.filter((p) => !existingIds.has(p.id))
      return {
        plans: [...s.plans, ...deduped],
        nextCursor: result.nextCursor,
        hasMore: result.hasMore,
      }
    })
  } catch (err) {
    if (isStaleListRequest(token)) return
    set({ listError: getErrorMessage(err) })
  } finally {
    if (!isStaleListRequest(token)) set({ listLoading: false })
  }
}

function clearDetailImpl(set: PlansSet): void {
  bumpDetailRequestToken()
  set({ selectedPlan: null, detailLoading: false, detailError: null })
}

export function createListActions(set: PlansSet, get: PlansGet) {
  return {
    fetchPlans: () => fetchPlansImpl(set),
    fetchMorePlans: () => fetchMorePlansImpl(set, get),
    setStatusFilter: (status: PlanStatus | null) => set({ statusFilter: status }),
    clearDetail: () => clearDetailImpl(set),
  }
}
