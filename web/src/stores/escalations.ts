/**
 * Escalation queue store (#1418).
 *
 * Mutations follow the canonical store error contract from
 * ``web/CLAUDE.md`` Zustand store error handling: log + error toast +
 * sentinel return on failure, callers MUST NOT wrap in try/catch.
 */
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

    submitting: false,

    fetchEscalations: async () => {
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
        set({
          escalations: page.data,
          total: page.total,
          nextCursor: page.nextCursor,
          hasMore: page.hasMore,
          loading: false,
        })
      } catch (err) {
        log.warn('Failed to fetch escalations:', getErrorMessage(err))
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
      set({ loadingMore: true })
      try {
        const page = await apiListEscalations({
          ...buildFilters(),
          cursor: state.nextCursor,
        })
        set({
          escalations: [...state.escalations, ...page.data],
          nextCursor: page.nextCursor,
          hasMore: page.hasMore,
          loadingMore: false,
        })
      } catch (err) {
        log.warn('Failed to fetch more escalations:', getErrorMessage(err))
        set({ loadingMore: false, error: getErrorMessage(err) })
      }
    },

    setStatusFilter: (status) => {
      set({ statusFilter: status })
      void get().fetchEscalations()
    },

    fetchEscalationDetail: async (id: string) => {
      set({ detailLoading: true, detailError: null, selected: null })
      try {
        const response = await apiGetEscalation(id)
        set({ selected: response, detailLoading: false })
      } catch (err) {
        log.warn('Failed to fetch escalation detail:', getErrorMessage(err))
        set({ detailLoading: false, detailError: getErrorMessage(err) })
      }
    },

    clearDetail: () => set({ selected: null, detailError: null }),

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
