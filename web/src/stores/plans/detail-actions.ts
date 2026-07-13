import {
  editPlan as editPlanApi,
  getPlan,
  requestPlanChanges as requestPlanChangesApi,
} from '@/api/endpoints/plans'
import { getTask } from '@/api/endpoints/tasks'
import type { EditPlanRequest, Plan } from '@/api/types/plans'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'

import { isStaleDetailRequest, nextDetailRequestToken } from './_state'
import type { PlansSet } from './types'

const log = createLogger('plans')

function upsertPlan(set: PlansSet, plan: Plan): void {
  // A mutation result is authoritative, so retire any in-flight detail read:
  // an older getPlan() must not resolve afterwards and clobber selectedPlan.
  // The retired fetch skips its own `finally`, so clear detailLoading here or
  // the pane would stay on the spinner until navigation.
  nextDetailRequestToken()
  set((state) => ({
    plans: state.plans.some((p) => p.id === plan.id)
      ? state.plans.map((p) => (p.id === plan.id ? plan : p))
      : [...state.plans, plan],
    selectedPlan: state.selectedPlan?.id === plan.id ? plan : state.selectedPlan,
    detailLoading: false,
  }))
}

/**
 * Resolve the plan's human headline from its parent objective task. Purely
 * decorative: the review surface falls back to the objective id, so a missing
 * or unreachable task is swallowed (logged at debug) rather than surfaced.
 */
async function resolveParentTaskTitle(
  set: PlansSet,
  token: number,
  parentTaskId: string,
): Promise<void> {
  try {
    const task = await getTask(parentTaskId)
    if (isStaleDetailRequest(token)) return
    set({ parentTaskTitle: task.title })
  } catch (err) {
    log.debug('Parent task title unresolved:', sanitizeForLog(err))
  }
}

async function fetchPlanDetailImpl(set: PlansSet, id: string): Promise<void> {
  const token = nextDetailRequestToken()
  set({
    detailLoading: true,
    detailError: null,
    selectedPlan: null,
    parentTaskTitle: null,
  })
  try {
    const plan = await getPlan(id)
    if (isStaleDetailRequest(token)) return
    // Render the detail immediately; the decorative headline fills in after,
    // so the page never blocks its loading state on the parent-task lookup.
    set({ selectedPlan: plan, detailLoading: false })
    await resolveParentTaskTitle(set, token, plan.parent_task_id)
  } catch (err) {
    if (isStaleDetailRequest(token)) return
    set({ detailError: getErrorMessage(err) })
  } finally {
    if (!isStaleDetailRequest(token)) set({ detailLoading: false })
  }
}

async function editPlanImpl(
  set: PlansSet,
  id: string,
  data: EditPlanRequest,
): Promise<Plan | null> {
  try {
    const plan = await editPlanApi(id, data)
    upsertPlan(set, plan)
    useToastStore.getState().add({
      variant: 'success',
      title: `Plan revised (v${String(plan.version)})`,
    })
    return plan
  } catch (err) {
    log.error('Edit plan failed:', sanitizeForLog(err))
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Failed to revise plan'),
      description: getErrorMessage(err),
    })
    return null
  }
}

async function requestPlanChangesImpl(
  set: PlansSet,
  id: string,
  note: string,
): Promise<Plan | null> {
  try {
    const plan = await requestPlanChangesApi(id, { note })
    upsertPlan(set, plan)
    useToastStore.getState().add({
      variant: 'success',
      title: 'Changes requested',
    })
    return plan
  } catch (err) {
    log.error('Request plan changes failed:', sanitizeForLog(err))
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Failed to request changes'),
      description: getErrorMessage(err),
    })
    return null
  }
}

export function createDetailActions(set: PlansSet) {
  return {
    fetchPlanDetail: (id: string) => fetchPlanDetailImpl(set, id),
    editPlan: (id: string, data: EditPlanRequest) => editPlanImpl(set, id, data),
    requestPlanChanges: (id: string, note: string) =>
      requestPlanChangesImpl(set, id, note),
  }
}
