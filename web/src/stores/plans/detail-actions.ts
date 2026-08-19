import {
  deletePlan as deletePlanApi,
  editPlan as editPlanApi,
  getPlan,
  requestPlanChanges as requestPlanChangesApi,
} from '@/api/endpoints/plans'
import type { EditPlanRequest, Plan } from '@/api/types/plans'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'

import { isStaleDetailRequest, nextDetailRequestToken } from './_state'
import type { PlansGet, PlansSet } from './types'

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

async function fetchPlanDetailImpl(
  set: PlansSet,
  get: PlansGet,
  id: string,
): Promise<void> {
  const token = nextDetailRequestToken()
  // Clearing the plan is right when moving to a DIFFERENT one, whose data
  // this is not, and wrong when re-reading the same one: the page renders a
  // full-height skeleton whenever it has no plan, so every refresh of the
  // open plan blanks what the operator is reading and brings it back. That
  // is one flash per answered question on the review page.
  const showing = get().selectedPlan
  const staying = showing?.id === id
  set({
    detailLoading: true,
    detailError: null,
    ...(staying ? {} : { selectedPlan: null }),
  })
  try {
    const plan = await getPlan(id)
    if (isStaleDetailRequest(token)) return
    set({ selectedPlan: plan })
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

async function deletePlanImpl(set: PlansSet, id: string): Promise<boolean> {
  try {
    await deletePlanApi(id)
    // Dropped locally as well as over the WS event, so the row goes on the
    // click that removed it rather than on the round trip back.
    nextDetailRequestToken()
    set((state) => ({
      plans: state.plans.filter((p) => p.id !== id),
      selectedPlan: state.selectedPlan?.id === id ? null : state.selectedPlan,
      detailLoading: false,
    }))
    useToastStore.getState().add({ variant: 'success', title: 'Plan deleted' })
    return true
  } catch (err) {
    log.error('Delete plan failed:', sanitizeForLog(err))
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Failed to delete plan'),
      description: getErrorMessage(err),
    })
    return false
  }
}

export function createDetailActions(set: PlansSet, get: PlansGet) {
  return {
    fetchPlanDetail: (id: string) => fetchPlanDetailImpl(set, get, id),
    editPlan: (id: string, data: EditPlanRequest) => editPlanImpl(set, id, data),
    deletePlan: (id: string) => deletePlanImpl(set, id),
    requestPlanChanges: (id: string, note: string) =>
      requestPlanChangesImpl(set, id, note),
  }
}
