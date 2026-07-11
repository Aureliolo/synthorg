import type { WsEvent } from '@/api/types'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { sanitizeWsString } from '@/utils/ws-sanitize'

import type { PlansGet } from './types'

const log = createLogger('plans')

const PLAN_EVENT_TYPES = new Set(['plan.updated', 'plan.changes_requested'])

function updateFromWsEventImpl(get: PlansGet, event: WsEvent): void {
  if (!PLAN_EVENT_TYPES.has(event.event_type)) return
  const planId = sanitizeWsString(event.payload['plan_id']) ?? null
  // Refresh the open detail view first so an edit/decision landing over WS is
  // reflected immediately; then refetch the list. Incremental payload merges
  // are not worth the complexity given the list is a small review inbox.
  if (planId && get().selectedPlan?.id === planId) {
    get()
      .fetchPlanDetail(planId)
      .catch((err: unknown) => log.warn('plan ws detail refetch failed', sanitizeForLog(err)))
  }
  get()
    .fetchPlans()
    .catch((err: unknown) => log.warn('plans ws refetch failed', sanitizeForLog(err)))
}

export function createWsHandler(get: PlansGet) {
  return {
    updateFromWsEvent: (event: WsEvent) => updateFromWsEventImpl(get, event),
  }
}
