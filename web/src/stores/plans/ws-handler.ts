import type { WsEvent } from '@/api/types/websocket'
import { createLogger } from '@/lib/logger'
import { usePlanCommentsStore } from '@/stores/planComments'
import { sanitizeForLog } from '@/utils/logging'
import { sanitizeWsString } from '@/utils/ws-sanitize'

import type { PlansGet } from './types'

const log = createLogger('plans')

const PLAN_EVENT_TYPES = new Set(['plan.updated', 'plan.changes_requested'])

// A comment landing over WS refreshes the open plan's thread so a second
// reviewer's comment appears live, without touching the plan row itself.
function refreshCommentsForOpenPlan(get: PlansGet, planId: string | null): void {
  if (planId && get().selectedPlan?.id === planId) {
    void usePlanCommentsStore.getState().fetchComments(planId)
  }
}

function updateFromWsEventImpl(get: PlansGet, event: WsEvent): void {
  const planId = sanitizeWsString(event.payload['plan_id']) ?? null
  if (event.event_type === 'plan.comment_added') {
    refreshCommentsForOpenPlan(get, planId)
    return
  }
  if (!PLAN_EVENT_TYPES.has(event.event_type)) return
  const refreshList = () =>
    get()
      .fetchPlans()
      .catch((err: unknown) => log.warn('plans ws refetch failed', sanitizeForLog(err)))
  // Refresh the open detail view first so an edit/decision landing over WS is
  // reflected immediately, then the list (even if the detail read failed), so
  // the list never resolves ahead of the detail. Incremental payload merges
  // are not worth the complexity given the list is a small review inbox.
  if (planId && get().selectedPlan?.id === planId) {
    void get()
      .fetchPlanDetail(planId)
      .catch((err: unknown) => log.warn('plan ws detail refetch failed', sanitizeForLog(err)))
      .finally(refreshList)
    return
  }
  void refreshList()
}

export function createWsHandler(get: PlansGet) {
  return {
    updateFromWsEvent: (event: WsEvent) => updateFromWsEventImpl(get, event),
  }
}
