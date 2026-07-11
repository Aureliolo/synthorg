import type { StoreApi } from 'zustand'

import type { EditPlanRequest, Plan, PlanStatus, WsEvent } from '@/api/types'

export interface PlansState {
  // List page. The review inbox filters across the whole set, so every
  // cursor page is walked on load (see web/CLAUDE.md client-side pagination).
  plans: readonly Plan[]
  listLoading: boolean
  listError: string | null

  // Filter (client-side; the full set is loaded and paged in the browser)
  statusFilter: PlanStatus | null

  // Detail page
  selectedPlan: Plan | null
  detailLoading: boolean
  detailError: string | null

  // Actions. Mutations follow the canonical store error contract:
  // log + error toast + return sentinel (`null`) on failure.
  fetchPlans: () => Promise<void>
  fetchPlanDetail: (id: string) => Promise<void>
  editPlan: (id: string, data: EditPlanRequest) => Promise<Plan | null>
  requestPlanChanges: (id: string, note: string) => Promise<Plan | null>
  setStatusFilter: (status: PlanStatus | null) => void
  clearDetail: () => void
  updateFromWsEvent: (event: WsEvent) => void
}

export type PlansSet = StoreApi<PlansState>['setState']
export type PlansGet = StoreApi<PlansState>['getState']
