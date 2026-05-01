/**
 * Escalation queue store.
 *
 * Mutations follow the canonical store error contract from
 * ``web/CLAUDE.md`` Zustand store error handling: log + error toast +
 * sentinel return on failure, callers MUST NOT wrap in try/catch.
 */
/* eslint-disable security/detect-possible-timing-attacks --
   Comparisons against in-flight request tokens (plain monotonic
   ints) are not timing-sensitive secrets; they are how this store
   discards stale fetch responses. */
import { create } from 'zustand'

import {
  cancelEscalation as apiCancelEscalation,
  getEscalation as apiGetEscalation,
  listEscalations as apiListEscalations,
  submitEscalationDecision as apiSubmitDecision,
  type ListEscalationsFilters,
} from '@/api/endpoints/escalations'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getErrorMessage } from '@/utils/errors'
import type {
  CancelEscalationRequest,
  EscalationResponse,
  EscalationStatus,
  SubmitDecisionRequest,
} from '@/api/types/escalations'

const log = createLogger('escalations')

interface EscalationsState {
  // List state
  escalations: readonly EscalationResponse[]
  total: number | null
  nextCursor: string | null
  hasMore: boolean
  loading: boolean
  loadingMore: boolean
  error: string | null

  // Filters
  statusFilter: EscalationStatus | null

  // Detail state
  selected: EscalationResponse | null
  detailLoading: boolean
  detailError: string | null
  /** The id whose detail fetch is currently active (or null). */
  detailRequestedId: string | null

  // Mutation state
  submitting: boolean

  // Actions
  fetchEscalations: () => Promise<void>
  fetchMoreEscalations: () => Promise<void>
  setStatusFilter: (status: EscalationStatus | null) => void
  fetchEscalationDetail: (id: string) => Promise<void>
  clearDetail: () => void
  submitDecision: (
    id: string,
    data: SubmitDecisionRequest,
  ) => Promise<EscalationResponse | null>
  cancelEscalation: (
    id: string,
    data: CancelEscalationRequest,
  ) => Promise<EscalationResponse | null>
}

export const useEscalationsStore = create<EscalationsState>()((set, get) => {
  // Monotonic request tokens used to discard stale fetch results.
  // ``listRequestToken`` covers both ``fetchEscalations`` and
  // ``fetchMoreEscalations``; ``detailRequestToken`` covers
  // ``fetchEscalationDetail`` and is invalidated by
  // ``clearDetail``.
  let listRequestToken = 0
  let detailRequestToken = 0

  const buildFilters = (): ListEscalationsFilters => {
    const filters: ListEscalationsFilters = {}
    const status = get().statusFilter
    if (status !== null) {
      // ``ListEscalationsFilters.status`` is readonly; the type allows
      // assignment via the literal-object spread above, so we widen here
      // when we have a concrete value.
      Object.assign(filters, { status })
    }
    return filters
  }

  return {
    escalations: [],
    total: null,
    nextCursor: null,
    hasMore: false,
    loading: false,
    loadingMore: false,
    error: null,

    statusFilter: 'pending',

    selected: null,
    detailLoading: false,
    detailError: null,
    detailRequestedId: null,

    submitting: false,

    fetchEscalations: async () => {
      // Bump request token so any concurrent in-flight list / detail
      // fetch knows its result is stale and should be discarded.
      const token = ++listRequestToken
      set({
        escalations: [],
        nextCursor: null,
        hasMore: false,
        loading: true,
        loadingMore: false,
        error: null,
      })
      try {
        const page = await apiListEscalations(buildFilters())
        if (token !== listRequestToken) return
        set({
          escalations: page.data,
          total: page.data.length,
          nextCursor: page.nextCursor,
          hasMore: page.hasMore,
          loading: false,
        })
      } catch (err) {
        log.warn('Failed to fetch escalations:', getErrorMessage(err))
        if (token !== listRequestToken) return
        set({ loading: false, error: getErrorMessage(err) })
      }
    },

    fetchMoreEscalations: async () => {
      const state = get()
      if (
        !state.hasMore ||
        !state.nextCursor ||
        state.loading ||
        state.loadingMore
      ) {
        return
      }
      const token = listRequestToken
      set({ loadingMore: true })
      try {
        const page = await apiListEscalations({
          ...buildFilters(),
          cursor: state.nextCursor,
        })
        if (token !== listRequestToken) return
        set((s) => {
          const merged = [...s.escalations, ...page.data]
          return {
            escalations: merged,
            // Keep ``total`` consistent with the store's cursor-only
            // pagination: it is the in-memory display count, recomputed
            // after every append rather than carried over from the
            // initial fetch (which would go stale once more pages
            // landed).
            total: merged.length,
            nextCursor: page.nextCursor,
            hasMore: page.hasMore,
            loadingMore: false,
          }
        })
      } catch (err) {
        log.warn('Failed to fetch more escalations:', getErrorMessage(err))
        if (token !== listRequestToken) return
        set({ loadingMore: false, error: getErrorMessage(err) })
      }
    },

    setStatusFilter: (status) => {
      set({ statusFilter: status })
      void get().fetchEscalations()
    },

    fetchEscalationDetail: async (id: string) => {
      // Bump the detail token so a slower previous fetch (or a
      // ``clearDetail()``) cannot overwrite this one's result.
      const token = ++detailRequestToken
      set({
        detailLoading: true,
        detailError: null,
        selected: null,
        detailRequestedId: id,
      })
      try {
        const response = await apiGetEscalation(id)
        if (token !== detailRequestToken) return
        set({ selected: response, detailLoading: false })
      } catch (err) {
        log.warn('Failed to fetch escalation detail:', getErrorMessage(err))
        if (token !== detailRequestToken) return
        set({ detailLoading: false, detailError: getErrorMessage(err) })
      }
    },

    clearDetail: () => {
      // Invalidate any in-flight detail fetch so its result cannot
      // re-populate ``selected`` after the drawer closes.  Also
      // clear ``detailLoading`` so the store does not get stuck in
      // a phantom loading state when ``clearDetail`` runs while a
      // fetch is still pending (the in-flight callback bails on
      // the token mismatch and never flips the flag itself).
      detailRequestToken++
      set({
        selected: null,
        detailError: null,
        detailRequestedId: null,
        detailLoading: false,
      })
    },

    submitDecision: async (id, data) => {
      set({ submitting: true })
      try {
        const response = await apiSubmitDecision(id, data)
        useToastStore.getState().add({
          variant: 'success',
          title: 'Escalation decided',
        })
        // Refresh the list so the decided row falls out of pending.
        void get().fetchEscalations()
        set({ submitting: false })
        return response
      } catch (err) {
        log.warn('Failed to submit escalation decision:', getErrorMessage(err))
        useToastStore.getState().add({
          variant: 'error',
          title: 'Failed to submit decision',
          description: getErrorMessage(err),
        })
        set({ submitting: false })
        return null
      }
    },

    cancelEscalation: async (id, data) => {
      set({ submitting: true })
      try {
        const response = await apiCancelEscalation(id, data)
        useToastStore.getState().add({
          variant: 'success',
          title: 'Escalation cancelled',
        })
        void get().fetchEscalations()
        set({ submitting: false })
        return response
      } catch (err) {
        log.warn('Failed to cancel escalation:', getErrorMessage(err))
        useToastStore.getState().add({
          variant: 'error',
          title: 'Failed to cancel escalation',
          description: getErrorMessage(err),
        })
        set({ submitting: false })
        return null
      }
    },
  }
})
