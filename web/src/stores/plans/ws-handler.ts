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

/**
 * The open plan this event touches, or null when it touches none.
 *
 * A replan announces the successor, so `plan_id` is an id the viewer sitting
 * on the retired plan does not hold. `supersedes` is the one that reaches
 * them, and without reading it their detail goes on showing a plan that has
 * been retired.
 */
function openPlanTouchedBy(get: PlansGet, event: WsEvent): string | null {
  const openId = get().selectedPlan?.id ?? null
  if (openId === null) return null
  const named = [
    sanitizeWsString(event.payload['plan_id']),
    sanitizeWsString(event.payload['supersedes']),
  ]
  return named.includes(openId) ? openId : null
}

function updateFromWsEventImpl(get: PlansGet, event: WsEvent): void {
  if (event.event_type === 'plan.comment_added') {
    refreshCommentsForOpenPlan(get, sanitizeWsString(event.payload['plan_id']) ?? null)
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
  const openId = openPlanTouchedBy(get, event)
  if (openId !== null) {
    void get()
      .fetchPlanDetail(openId)
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
