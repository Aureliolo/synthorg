import type { StoreApi } from 'zustand'
import { create } from 'zustand'
import * as charterApi from '@/api/endpoints/charter'
import type { CharterFilters } from '@/api/endpoints/charter'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { createLogger } from '@/lib/logger'
import type {
  CharterApprovalResult,
  CharterEditRequest,
  InterviewTurnResult,
  ProjectCharter,
} from '@/api/types'

const log = createLogger('charter')

interface CharterState {
  charters: ProjectCharter[]
  loading: boolean
  error: string | null
  nextCursor: string | null
  hasMore: boolean

  draftCharter: ProjectCharter | null
  /** True while an edit / approve / cancel mutation is in flight. */
  mutating: boolean

  fetchCharters: (filters?: CharterFilters) => Promise<void>
  fetchMoreCharters: (filters?: CharterFilters) => Promise<void>
  /**
   * Adopt a charter-interview turn resolved through the unified org
   * conversation ({@link postTurn}), so the draft side panel renders the
   * drafted charter and its edit/approve/cancel actions target it.
   */
  hydrateFromTurn: (turn: InterviewTurnResult) => void
  editDraft: (
    id: string,
    data: CharterEditRequest,
  ) => Promise<ProjectCharter | null>
  approve: (id: string) => Promise<CharterApprovalResult | null>
  cancel: (id: string) => Promise<boolean>
  resetInterview: () => void
}

type CharterSet = StoreApi<CharterState>['setState']
type CharterGet = StoreApi<CharterState>['getState']

async function fetchChartersImpl(
  set: CharterSet,
  filters?: CharterFilters,
): Promise<void> {
  set({ loading: true, error: null })
  try {
    const page = await charterApi.listCharters(filters)
    set({
      charters: page.data,
      nextCursor: page.nextCursor,
      hasMore: page.hasMore,
      loading: false,
    })
  } catch (err) {
    log.warn('Failed to fetch charters', sanitizeForLog(err))
    set({ loading: false, error: getErrorMessage(err) })
  }
}

async function fetchMoreChartersImpl(
  set: CharterSet,
  get: CharterGet,
  filters?: CharterFilters,
): Promise<void> {
  const { hasMore, nextCursor, loading } = get()
  if (!hasMore || !nextCursor || loading) return
  set({ loading: true })
  try {
    const page = await charterApi.listCharters({
      ...filters,
      cursor: nextCursor,
    })
    set((state) => ({
      charters: [...state.charters, ...page.data],
      nextCursor: page.nextCursor,
      hasMore: page.hasMore,
      loading: false,
    }))
  } catch (err) {
    log.warn('Failed to fetch more charters', sanitizeForLog(err))
    set({ loading: false, error: getErrorMessage(err) })
  }
}

async function editDraftImpl(
  set: CharterSet,
  id: string,
  data: CharterEditRequest,
): Promise<ProjectCharter | null> {
  set({ mutating: true })
  try {
    const updated = await charterApi.editCharter(id, data)
    set({ draftCharter: updated })
    useToastStore.getState().add({ variant: 'success', title: 'Charter updated' })
    return updated
  } catch (err) {
    log.error('Charter edit failed', sanitizeForLog(err))
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Could not update the charter'),
      description: getErrorMessage(err),
    })
    return null
  } finally {
    set({ mutating: false })
  }
}

async function approveImpl(
  set: CharterSet,
  id: string,
): Promise<CharterApprovalResult | null> {
  set({ mutating: true })
  try {
    const result = await charterApi.approveCharter(id)
    set({ draftCharter: result.charter })
    if (result.is_success) {
      useToastStore.getState().add({
        variant: 'success',
        title: 'Charter approved',
        description: 'The project run has started.',
      })
    } else {
      // The charter was approved (a human decided) but the run produced no
      // successful work: e.g. decomposition failed. Surface that instead of a
      // false success. The failure is durable and visible as a FAILED plan in
      // Plan Review, so the operator can inspect the reason and start a new run.
      useToastStore.getState().add({
        variant: 'error',
        title: 'Charter approved, but the run failed',
        description:
          'The objective could not be prepared into a plan. Open Plan Review for the failed plan and its reason, then start a new project run.',
      })
    }
    return result
  } catch (err) {
    log.error('Charter approval failed', sanitizeForLog(err))
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Could not approve the charter'),
      description: getErrorMessage(err),
    })
    return null
  } finally {
    set({ mutating: false })
  }
}

async function cancelImpl(set: CharterSet, id: string): Promise<boolean> {
  set({ mutating: true })
  try {
    const cancelled = await charterApi.cancelCharter(id)
    set({ draftCharter: cancelled })
    useToastStore.getState().add({ variant: 'success', title: 'Charter cancelled' })
    return true
  } catch (err) {
    log.error('Charter cancel failed', sanitizeForLog(err))
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Could not cancel the charter'),
      description: getErrorMessage(err),
    })
    return false
  } finally {
    set({ mutating: false })
  }
}

export const useCharterStore = create<CharterState>()((set, get) => ({
  charters: [],
  loading: false,
  error: null,
  nextCursor: null,
  hasMore: false,
  draftCharter: null,
  mutating: false,

  fetchCharters: (filters) => fetchChartersImpl(set, filters),
  fetchMoreCharters: (filters) => fetchMoreChartersImpl(set, get, filters),
  hydrateFromTurn: (turn) =>
    set({ draftCharter: turn.charter ?? get().draftCharter }),

  editDraft: (id, data) => editDraftImpl(set, id, data),
  approve: (id) => approveImpl(set, id),
  cancel: (id) => cancelImpl(set, id),

  resetInterview: () => {
    set({ draftCharter: null, mutating: false })
  },
}))
