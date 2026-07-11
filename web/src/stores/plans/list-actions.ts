import { paginateAll } from '@/api/client'
import { listPlans } from '@/api/endpoints/plans'
import type { Plan, PlanStatus } from '@/api/types/plans'
import { createLogger } from '@/lib/logger'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'

import { bumpDetailRequestToken, isStaleListRequest, nextListRequestToken } from './_state'
import type { PlansSet } from './types'

const log = createLogger('plans')

const PLANS_PAGE_LIMIT = 200

async function fetchPlansImpl(set: PlansSet): Promise<void> {
  const token = nextListRequestToken()
  set({ listLoading: true, listError: null })
  try {
    // Walk every cursor page: the review inbox filters/sorts across the whole
    // set client-side, so a single capped page would silently hide plans.
    const plans = await paginateAll<Plan>((cursor) =>
      listPlans({ cursor, limit: PLANS_PAGE_LIMIT }),
    )
    if (isStaleListRequest(token)) return
    set({ plans })
  } catch (err) {
    if (isStaleListRequest(token)) return
    log.error('Failed to fetch plans:', sanitizeForLog(err))
    set({ listError: getErrorMessage(err) })
  } finally {
    if (!isStaleListRequest(token)) set({ listLoading: false })
  }
}

function clearDetailImpl(set: PlansSet): void {
  bumpDetailRequestToken()
  set({ selectedPlan: null, detailLoading: false, detailError: null })
}

export function createListActions(set: PlansSet) {
  return {
    fetchPlans: () => fetchPlansImpl(set),
    setStatusFilter: (status: PlanStatus | null) => set({ statusFilter: status }),
    clearDetail: () => clearDetailImpl(set),
  }
}
