import { create } from 'zustand'
import {
  listSubworkflows,
  searchSubworkflows,
  deleteSubworkflow as deleteSubworkflowApi,
} from '@/api/endpoints/subworkflows'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import type { PaginatedResult } from '@/api/client'
import type { SubworkflowSummary } from '@/api/types/workflows'

const log = createLogger('subworkflows')

const PAGE_SIZE = 100

/**
 * Hard cap on how many subworkflows the store will eagerly drain into
 * memory across cursored pages. Subworkflows is a small registry by
 * design (reusable components, not a per-run artefact stream), but a
 * misconfigured deployment could still surface tens of thousands; the
 * cap keeps the dashboard from hanging on a runaway list while still
 * leaving plenty of headroom for the realistic case.
 */
const MAX_PAGES = 20

interface SubworkflowsState {
  subworkflows: readonly SubworkflowSummary[]
  listLoading: boolean
  listError: string | null
  searchQuery: string
  // ``true`` when the paged drain stopped at ``MAX_PAGES`` while the
  // server reported there were still more results. Callers can use this
  // to render an honest "showing first N" banner instead of pretending
  // the visible list is the whole registry.
  subworkflowsTruncated: boolean

  fetchSubworkflows: () => Promise<void>
  deleteSubworkflow: (id: string, version: string) => Promise<boolean>
  setSearchQuery: (q: string) => void
  updateFromWsEvent: () => void
}

let _listRequestToken = 0
function isStaleRequest(token: number): boolean {
  return _listRequestToken !== token
}

export const useSubworkflowsStore = create<SubworkflowsState>((set, get) => ({
  subworkflows: [],
  listLoading: false,
  listError: null,
  searchQuery: '',
  subworkflowsTruncated: false,

  async fetchSubworkflows() {
    const token = ++_listRequestToken
    set(() => ({
      listLoading: true,
      listError: null,
      subworkflows: [],
      subworkflowsTruncated: false,
    }))
    try {
      const query = get().searchQuery.trim()
      // Both the unfiltered list and the search endpoint are
      // cursor-paginated; drain cursored pages eagerly so the page can
      // render a numeric pager via useListPagination instead of a
      // "Load More" button. MAX_PAGES bounds the worst case. The user
      // expects to see every match, so a search drains the same way.
      const collected: SubworkflowSummary[] = []
      let cursor: string | null = null
      let truncated = false
      for (let pageIndex = 0; pageIndex < MAX_PAGES; pageIndex += 1) {
        const page: PaginatedResult<SubworkflowSummary> = query
          ? await searchSubworkflows(query, {
              cursor: cursor ?? undefined,
              limit: PAGE_SIZE,
            })
          : await listSubworkflows({
              cursor: cursor ?? undefined,
              limit: PAGE_SIZE,
            })
        if (isStaleRequest(token)) return
        collected.push(...page.data)
        if (!page.hasMore || !page.nextCursor) break
        cursor = page.nextCursor
        // The loop is about to exit on the next ``pageIndex`` increment
        // while the server still has more results; signal that the
        // visible list is a prefix, not the whole registry.
        if (pageIndex === MAX_PAGES - 1) truncated = true
      }
      set(() => ({
        subworkflows: collected,
        listLoading: false,
        subworkflowsTruncated: truncated,
      }))
    } catch (err: unknown) {
      if (isStaleRequest(token)) return
      log.warn('Failed to fetch subworkflows', sanitizeForLog(err))
      set(() => ({
        listLoading: false,
        listError: getErrorMessage(err),
      }))
    }
  },

  async deleteSubworkflow(id: string, version: string) {
    try {
      await deleteSubworkflowApi(id, version)
      await get().fetchSubworkflows()
      useToastStore.getState().add({
        variant: 'success',
        title: 'Subworkflow deleted',
      })
      return true
    } catch (err) {
      log.error('Delete subworkflow failed', sanitizeForLog(err))
      useToastStore.getState().add({
        variant: 'error',
        title: 'Failed to delete subworkflow',
        description: getErrorMessage(err),
      })
      return false
    }
  },

  setSearchQuery(q: string) {
    set(() => ({ searchQuery: q }))
  },

  updateFromWsEvent() {
    get().fetchSubworkflows().catch((err: unknown) => {
      log.warn('subworkflows ws refetch failed', sanitizeForLog(err))
    })
  },
}))
