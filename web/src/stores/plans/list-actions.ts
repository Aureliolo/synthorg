import { paginateAll } from '@/api/client'
import { listPlans } from '@/api/endpoints/plans'
import type { Plan, PlanStatus } from '@/api/types/plans'
import { createLogger } from '@/lib/logger'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'

import { bumpDetailRequestToken, isStaleListRequest, nextListRequestToken } from './_state'
import { resolvePlanTitles } from './title-resolution'
import type { PlansGet, PlansSet } from './types'

const log = createLogger('plans')

const PLANS_PAGE_LIMIT = 200

/**
 * Merge freshly-resolved headlines over the cached ones, keeping only titles
 * for plans still present. Prunes entries for plans that left the inbox so the
 * map does not grow unbounded across refreshes.
 */
function mergePlanTitles(
  plans: readonly Plan[],
  cached: Record<string, string>,
  resolved: Record<string, string>,
): Record<string, string> {
  const merged: Record<string, string> = {}
  for (const plan of plans) {
    const title = resolved[plan.id] ?? cached[plan.id]
    if (title !== undefined) merged[plan.id] = title
  }
  return merged
}

async function fetchPlansImpl(set: PlansSet, get: PlansGet): Promise<void> {
  const token = nextListRequestToken()
  set({ listLoading: true, listError: null })
  try {
    // Walk every cursor page: the review inbox filters/sorts across the whole
    // set client-side, so a single capped page would silently hide plans.
    const plans = await paginateAll<Plan>((cursor) =>
      listPlans({ cursor, limit: PLANS_PAGE_LIMIT }),
    )
    if (isStaleListRequest(token)) return
    // Render the list immediately; the human headlines fill in as the parent
    // objective tasks resolve, so a slow lookup never blocks the inbox.
    set({ plans, listLoading: false })
    // Only resolve plans without a cached title: a refresh (WS resync, refocus)
    // must not re-fetch every parent task it already knows.
    const cached = get().planTitles
    const unresolved = plans.filter((plan) => cached[plan.id] === undefined)
    const resolved = await resolvePlanTitles(unresolved)
    if (!isStaleListRequest(token)) {
      set({ planTitles: mergePlanTitles(plans, cached, resolved) })
    }
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
  set({
    selectedPlan: null,
    detailLoading: false,
    detailError: null,
    parentTaskTitle: null,
  })
}

export function createListActions(set: PlansSet, get: PlansGet) {
  return {
    fetchPlans: () => fetchPlansImpl(set, get),
    setStatusFilter: (status: PlanStatus | null) => set({ statusFilter: status }),
    clearDetail: () => clearDetailImpl(set),
  }
}
