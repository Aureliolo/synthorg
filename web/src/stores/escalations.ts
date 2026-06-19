/**
 * Escalation queue store.
 *
 * Mutations follow the canonical store error contract from
 * ``web/CLAUDE.md`` Zustand store error handling: log + error toast +
 * sentinel return on failure, callers MUST NOT wrap in try/catch.
 */
import type { StoreApi } from 'zustand'
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
  escalations: readonly EscalationResponse[]
  total: number | null
  nextCursor: string | null
  hasMore: boolean
  loading: boolean
  loadingMore: boolean
  error: string | null

  statusFilter: EscalationStatus | null

  selected: EscalationResponse | null
  detailLoading: boolean
  detailError: string | null
  detailRequestedId: string | null

  submitting: boolean

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

type EscSet = StoreApi<EscalationsState>['setState']
type EscGet = StoreApi<EscalationsState>['getState']

let listRequestToken = 0
let detailRequestToken = 0

function buildFilters(get: EscGet): ListEscalationsFilters {
  const filters: ListEscalationsFilters = {}
  const status = get().statusFilter
  if (status !== null) {
    Object.assign(filters, { status })
  }
  return filters
}

async function fetchEscalationsImpl(
  set: EscSet,
  get: EscGet,
): Promise<void> {
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
    const page = await apiListEscalations(buildFilters(get))
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
}

async function fetchMoreEscalationsImpl(
  set: EscSet,
  get: EscGet,
): Promise<void> {
  const state = get()
  if (
    !state.hasMore
    || !state.nextCursor
    || state.loading
    || state.loadingMore
  ) {
    return
  }
  const token = listRequestToken
  set({ loadingMore: true })
  try {
    const page = await apiListEscalations({
      ...buildFilters(get),
      cursor: state.nextCursor,
    })
    if (token !== listRequestToken) return
    set((s) => {
      const merged = [...s.escalations, ...page.data]
      return {
        escalations: merged,
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
}

async function fetchEscalationDetailImpl(
  set: EscSet,
  id: string,
): Promise<void> {
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
}

function clearDetailImpl(set: EscSet): void {
  detailRequestToken++
  set({
    selected: null,
    detailError: null,
    detailRequestedId: null,
    detailLoading: false,
  })
}

async function submitDecisionImpl(
  set: EscSet,
  get: EscGet,
  id: string,
  data: SubmitDecisionRequest,
): Promise<EscalationResponse | null> {
  set({ submitting: true })
  try {
    const response = await apiSubmitDecision(id, data)
    useToastStore.getState().add({
      variant: 'success',
      title: 'Escalation decided',
    })
    get().fetchEscalations().catch((refetchErr: unknown) => {
      log.warn(
        'escalations post-decision refetch failed',
        getErrorMessage(refetchErr),
      )
    })
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
}

async function cancelEscalationImpl(
  set: EscSet,
  get: EscGet,
  id: string,
  data: CancelEscalationRequest,
): Promise<EscalationResponse | null> {
  set({ submitting: true })
  try {
    const response = await apiCancelEscalation(id, data)
    useToastStore.getState().add({
      variant: 'success',
      title: 'Escalation cancelled',
    })
    get().fetchEscalations().catch((refetchErr: unknown) => {
      log.warn(
        'escalations post-cancel refetch failed',
        getErrorMessage(refetchErr),
      )
    })
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
}

export const useEscalationsStore = create<EscalationsState>()((set, get) => ({
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

  fetchEscalations: () => fetchEscalationsImpl(set, get),
  fetchMoreEscalations: () => fetchMoreEscalationsImpl(set, get),
  setStatusFilter: (status) => {
    set({ statusFilter: status })
    get().fetchEscalations().catch((err: unknown) => {
      log.warn(
        'escalations filter-change refetch failed',
        getErrorMessage(err),
      )
    })
  },
  fetchEscalationDetail: (id) => fetchEscalationDetailImpl(set, id),
  clearDetail: () => clearDetailImpl(set),
  submitDecision: (id, data) => submitDecisionImpl(set, get, id, data),
  cancelEscalation: (id, data) => cancelEscalationImpl(set, get, id, data),
}))
