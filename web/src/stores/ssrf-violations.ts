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
  /**
   * Failure of a "load more" page fetch. Tracked separately from ``error`` so a
   * pagination failure stays observable to state-driven consumers (the list-read
   * contract) WITHOUT the page-level ``error`` banner hiding the already-loaded
   * violations. Surfaced inline next to the Load-more control.
   */
  loadMoreError: string | null
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
  // Clear loadingMore too: a fetchMore in flight when this full refetch starts
  // exits on the token mismatch without resetting its own flag, so without this
  // reset loadingMore could stay true and permanently lock pagination.
  set({
    violations: [],
    nextCursor: null,
    hasMore: false,
    loading: true,
    loadingMore: false,
    error: null,
    loadMoreError: null,
  })
  try {
    const page = await apiListViolations(buildFilters(get))
    if (token !== listRequestToken) return
    set({
      violations: page.data,
      nextCursor: page.nextCursor,
      hasMore: page.hasMore,
      loading: false,
      loadingMore: false,
    })
  } catch (err) {
    log.warn('Failed to fetch SSRF violations', sanitizeForLog(getErrorMessage(err)))
    if (token !== listRequestToken) return
    set({ loading: false, loadingMore: false, error: getErrorMessage(err) })
  }
}

async function fetchMoreViolationsImpl(set: Set, get: Get): Promise<void> {
  const state = get()
  if (!state.hasMore || !state.nextCursor || state.loading || state.loadingMore) return
  const token = listRequestToken
  set({ loadingMore: true, loadMoreError: null })
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
    const message = getErrorMessage(err)
    // Record the failure in a dedicated field (not the page-level ``error``,
    // which would hide the already-loaded list) so it stays observable to
    // state-driven consumers per the list-read contract, then also toast it.
    set({ loadingMore: false, loadMoreError: message })
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Failed to load more violations'),
      description: message,
    })
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
  loadMoreError: null,
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
