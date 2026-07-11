import type { StoreApi } from 'zustand'

import type { EditPlanRequest, Plan, PlanStatus, WsEvent } from '@/api/types'

export interface PlansState {
  // List page
  plans: readonly Plan[]
  /** Opaque cursor for the next page; null on the final page. */
  nextCursor: string | null
  /** Whether more items follow the current page. */
  hasMore: boolean
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
  fetchMorePlans: () => Promise<void>
  fetchPlanDetail: (id: string) => Promise<void>
  editPlan: (id: string, data: EditPlanRequest) => Promise<Plan | null>
  requestPlanChanges: (id: string, note: string) => Promise<Plan | null>
  setStatusFilter: (status: PlanStatus | null) => void
  clearDetail: () => void
  updateFromWsEvent: (event: WsEvent) => void
}

export type PlansSet = StoreApi<PlansState>['setState']
export type PlansGet = StoreApi<PlansState>['getState']
