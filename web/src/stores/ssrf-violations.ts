/**
 * SSRF-violation review-queue store.
 *
 * Mutations follow the canonical store error contract from ``web/CLAUDE.md``:
 * log + error toast + sentinel return on failure; callers MUST NOT wrap in
 * try/catch. List reads set ``error`` on the store instead of toasting.
 */
import type { StoreApi } from 'zustand'
import { create } from 'zustand'

import {
  listSsrfViolations as apiListViolations,
  resolveSsrfViolation as apiResolveViolation,
  type ListSsrfViolationsFilters,
} from '@/api/endpoints/ssrf-violations'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import type { ResolveSsrfViolationRequest, SsrfViolationDTO } from '@/api/types'
import type { SsrfViolationStatus } from '@/api/types/enum-values.gen'

const log = createLogger('ssrf-violations')

interface SsrfViolationsState {
  violations: readonly SsrfViolationDTO[]
  nextCursor: string | null
  hasMore: boolean
  loading: boolean
  loadingMore: boolean
  error: string | null
  statusFilter: SsrfViolationStatus | null
  /** Id of the violation whose resolve is in flight. */
  resolvingId: string | null

  fetchViolations: () => Promise<void>
  fetchMoreViolations: () => Promise<void>
  setStatusFilter: (status: SsrfViolationStatus | null) => void
  resolveViolation: (
    id: string,
    status: ResolveSsrfViolationRequest['status'],
  ) => Promise<boolean>
}

type Set = StoreApi<SsrfViolationsState>['setState']
type Get = StoreApi<SsrfViolationsState>['getState']

let listRequestToken = 0

function buildFilters(get: Get): ListSsrfViolationsFilters {
  const status = get().statusFilter
  return status !== null ? { status } : {}
}

async function fetchViolationsImpl(set: Set, get: Get): Promise<void> {
  const token = ++listRequestToken
  set({ violations: [], nextCursor: null, hasMore: false, loading: true, error: null })
  try {
    const page = await apiListViolations(buildFilters(get))
    if (token !== listRequestToken) return
    set({
      violations: page.data,
      nextCursor: page.nextCursor,
      hasMore: page.hasMore,
      loading: false,
    })
  } catch (err) {
    log.warn('Failed to fetch SSRF violations', sanitizeForLog(getErrorMessage(err)))
    if (token !== listRequestToken) return
    set({ loading: false, error: getErrorMessage(err) })
  }
}

async function fetchMoreViolationsImpl(set: Set, get: Get): Promise<void> {
  const state = get()
  if (!state.hasMore || !state.nextCursor || state.loading || state.loadingMore) return
  const token = listRequestToken
  set({ loadingMore: true })
  try {
    const page = await apiListViolations({ ...buildFilters(get), cursor: state.nextCursor })
    if (token !== listRequestToken) return
    set((s) => ({
      violations: [...s.violations, ...page.data],
      nextCursor: page.nextCursor,
      hasMore: page.hasMore,
      loadingMore: false,
    }))
  } catch (err) {
    log.warn('Failed to fetch more SSRF violations', sanitizeForLog(getErrorMessage(err)))
    if (token !== listRequestToken) return
    set({ loadingMore: false, error: getErrorMessage(err) })
  }
}

async function resolveViolationImpl(
  set: Set,
  get: Get,
  id: string,
  status: ResolveSsrfViolationRequest['status'],
): Promise<boolean> {
  set({ resolvingId: id })
  try {
    await apiResolveViolation(id, status)
    useToastStore.getState().add({
      variant: 'success',
      title: status === 'allowed' ? 'Violation allowed' : 'Violation denied',
    })
    set({ resolvingId: null })
    // Refetch so the row leaves the pending view and counts stay accurate.
    get().fetchViolations().catch((err: unknown) => {
      log.warn('SSRF post-resolve refetch failed', sanitizeForLog(getErrorMessage(err)))
    })
    return true
  } catch (err) {
    log.warn('Failed to resolve SSRF violation', sanitizeForLog(getErrorMessage(err)))
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Could not resolve violation'),
      description: getErrorMessage(err),
    })
    set({ resolvingId: null })
    return false
  }
}

export const useSsrfViolationsStore = create<SsrfViolationsState>()((set, get) => ({
  violations: [],
  nextCursor: null,
  hasMore: false,
  loading: false,
  loadingMore: false,
  error: null,
  statusFilter: 'pending',
  resolvingId: null,

  fetchViolations: () => fetchViolationsImpl(set, get),
  fetchMoreViolations: () => fetchMoreViolationsImpl(set, get),
  setStatusFilter: (status) => {
    set({ statusFilter: status })
    get().fetchViolations().catch((err: unknown) => {
      log.warn('SSRF filter-change refetch failed', sanitizeForLog(getErrorMessage(err)))
    })
  },
  resolveViolation: (id, status) => resolveViolationImpl(set, get, id, status),
}))
