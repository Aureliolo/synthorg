import {
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
import type { PlansSet } from './types'

const log = createLogger('plans')

function upsertPlan(set: PlansSet, plan: Plan): void {
  // A mutation result is authoritative, so retire any in-flight detail read:
  // an older getPlan() must not resolve afterwards and clobber selectedPlan.
  nextDetailRequestToken()
  set((state) => ({
    plans: state.plans.some((p) => p.id === plan.id)
      ? state.plans.map((p) => (p.id === plan.id ? plan : p))
      : [...state.plans, plan],
    selectedPlan: state.selectedPlan?.id === plan.id ? plan : state.selectedPlan,
  }))
}

async function fetchPlanDetailImpl(set: PlansSet, id: string): Promise<void> {
  const token = nextDetailRequestToken()
  set({ detailLoading: true, detailError: null, selectedPlan: null })
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

export function createDetailActions(set: PlansSet) {
  return {
    fetchPlanDetail: (id: string) => fetchPlanDetailImpl(set, id),
    editPlan: (id: string, data: EditPlanRequest) => editPlanImpl(set, id, data),
    requestPlanChanges: (id: string, note: string) =>
      requestPlanChangesImpl(set, id, note),
  }
}
