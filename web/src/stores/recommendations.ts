import { create } from 'zustand'
import {
  approveRecommendation,
  getRefreshStatus,
  listModelRecommendations,
  rejectRecommendation,
  triggerRefresh,
} from '@/api/endpoints/recommendations'
import type { RefreshStatusDTO, UpgradeRecommendationDTO } from '@/api/types'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage, isAxiosError } from '@/utils/errors'

const log = createLogger('recommendations')

interface RecommendationsState {
  recommendations: readonly UpgradeRecommendationDTO[]
  listLoading: boolean
  listError: string | null
  status: RefreshStatusDTO | null
  statusLoading: boolean
  statusError: string | null
  refreshing: boolean
  /** Id of the recommendation whose approve/reject is in flight. */
  decidingId: string | null
  /**
   * True when the periodic model-refresh feature is disabled server-side
   * (``providers.model_refresh_mode = off``, the default). The endpoints
   * return 503 in that state; it is a steady-state, not a load error, so
   * consumers render a "disabled" hint rather than an error.
   */
  disabled: boolean

  fetchRecommendations: () => Promise<void>
  fetchStatus: () => Promise<void>
  runRefresh: () => Promise<boolean>
  approve: (id: string) => Promise<boolean>
  reject: (id: string) => Promise<boolean>
  reset: () => void
}

const INITIAL: Pick<
  RecommendationsState,
  | 'recommendations'
  | 'listLoading'
  | 'listError'
  | 'status'
  | 'statusLoading'
  | 'statusError'
  | 'refreshing'
  | 'decidingId'
  | 'disabled'
> = {
  recommendations: [],
  listLoading: false,
  listError: null,
  status: null,
  statusLoading: false,
  statusError: null,
  refreshing: false,
  decidingId: null,
  disabled: false,
}

/**
 * A 503 from the model-refresh surface means the feature is disabled
 * (default), not a failure. Distinguished so callers show a disabled hint
 * instead of logging an error and toasting.
 */
function isFeatureDisabled(err: unknown): boolean {
  return isAxiosError(err) && err.response?.status === 503
}

function dropById(
  items: readonly UpgradeRecommendationDTO[],
  id: string,
): readonly UpgradeRecommendationDTO[] {
  return items.filter((r) => r.id !== id)
}

export const useRecommendationsStore = create<RecommendationsState>((set, get) => ({
  ...INITIAL,

  reset: () => set({ ...INITIAL }),

  fetchRecommendations: async () => {
    // Feature is off (default): the endpoint only ever 503s, so once
    // learned, skip re-probing on every mount. A page reload resets the
    // store and re-checks, picking up a later enable.
    if (get().disabled) return
    set({ listLoading: true, listError: null })
    try {
      const recommendations = await listModelRecommendations('pending')
      set({ recommendations, listLoading: false, disabled: false })
    } catch (err) {
      if (isFeatureDisabled(err)) {
        set({ listLoading: false, disabled: true, recommendations: [] })
        return
      }
      log.error('fetchRecommendations:', getErrorMessage(err))
      set({ listLoading: false, listError: getErrorMessage(err) })
    }
  },

  fetchStatus: async () => {
    if (get().disabled) return
    set({ statusLoading: true, statusError: null })
    try {
      const status = await getRefreshStatus()
      set({ status, statusLoading: false, disabled: false })
    } catch (err) {
      if (isFeatureDisabled(err)) {
        set({ statusLoading: false, disabled: true })
        return
      }
      log.error('fetchStatus:', getErrorMessage(err))
      set({ statusLoading: false, statusError: getErrorMessage(err) })
    }
  },

  runRefresh: async () => {
    set({ refreshing: true })
    try {
      const report = await triggerRefresh()
      useToastStore.getState().add({
        variant: 'success',
        title: 'Refresh complete',
        description: `${report.recommended_count} recommendation(s), ${report.stale_count} stale model(s).`,
      })
      set({ refreshing: false })
      await get().fetchRecommendations()
      return true
    } catch (err) {
      log.error('runRefresh:', getErrorMessage(err))
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Could not run refresh'),
        description: getErrorMessage(err),
      })
      set({ refreshing: false })
      return false
    }
  },

  approve: async (id) => decide(set, get, id, 'approve'),
  reject: async (id) => decide(set, get, id, 'reject'),
}))

const DECISION_COPY = {
  approve: {
    verb: approveRecommendation,
    title: 'Recommendation approved',
    fail: 'Could not approve',
    event: 'approveRecommendation',
  },
  reject: {
    verb: rejectRecommendation,
    title: 'Recommendation rejected',
    fail: 'Could not reject',
    event: 'rejectRecommendation',
  },
} as const

async function decide(
  set: (partial: Partial<RecommendationsState>) => void,
  get: () => RecommendationsState,
  id: string,
  kind: 'approve' | 'reject',
): Promise<boolean> {
  const copy = DECISION_COPY[kind]
  set({ decidingId: id })
  try {
    await copy.verb(id)
    useToastStore.getState().add({ variant: 'success', title: copy.title })
    set({ recommendations: dropById(get().recommendations, id), decidingId: null })
    return true
  } catch (err) {
    log.error(copy.event, getErrorMessage(err))
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, copy.fail),
      description: getErrorMessage(err),
    })
    set({ decidingId: null })
    return false
  }
}
