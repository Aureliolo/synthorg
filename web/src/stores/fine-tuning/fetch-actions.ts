import {
  getFineTuneStatus,
  listCheckpoints,
  listRuns,
} from '@/api/endpoints/fine-tuning'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { createLogger } from '@/lib/logger'
import type { PaginatedResult } from '@/api/client'
import {
  DRAIN_PAGE_LIMIT,
  LIST_PAGE_SIZE,
  NO_MORE,
} from './_helpers'
import type {
  FineTuningSet,
  ListPagination,
} from './types'

const log = createLogger('fine-tuning-store')

type CursorPageFn<T> = (
  cursor: string | null,
  limit: number,
) => Promise<PaginatedResult<T>>

interface DrainResult<T> {
  data: T[]
  pagination: ListPagination
}

async function drainAllPages<T>(
  fetchPage: CursorPageFn<T>,
): Promise<DrainResult<T>> {
  const collected: T[] = []
  let pagination: ListPagination = NO_MORE
  let cursor: string | null = null
  for (let i = 0; i < DRAIN_PAGE_LIMIT; i++) {
    const page = await fetchPage(cursor, LIST_PAGE_SIZE)
    collected.push(...page.data)
    pagination = { nextCursor: page.nextCursor, hasMore: page.hasMore }
    if (!page.hasMore || !page.nextCursor) break
    cursor = page.nextCursor
  }
  return { data: collected, pagination }
}

async function fetchStatusImpl(set: FineTuningSet): Promise<void> {
  try {
    const status = await getFineTuneStatus()
    set((state) => ({
      status,
      errors: { ...state.errors, status: null },
    }))
  } catch (err) {
    log.error('Failed to fetch fine-tune status', sanitizeForLog(err))
    const message = getErrorMessage(err)
    set((state) => ({ errors: { ...state.errors, status: message } }))
  }
}

async function fetchCheckpointsImpl(set: FineTuningSet): Promise<void> {
  try {
    const { data, pagination } = await drainAllPages(listCheckpoints)
    set((state) => ({
      checkpoints: data,
      checkpointsPagination: pagination,
      errors: { ...state.errors, checkpoints: null },
    }))
  } catch (err) {
    log.error('Failed to fetch checkpoints', sanitizeForLog(err))
    const message = getErrorMessage(err)
    set((state) => ({
      errors: { ...state.errors, checkpoints: message },
    }))
  }
}

async function fetchRunsImpl(set: FineTuningSet): Promise<void> {
  try {
    const { data, pagination } = await drainAllPages(listRuns)
    set((state) => ({
      runs: data,
      runsPagination: pagination,
      errors: { ...state.errors, runs: null },
    }))
  } catch (err) {
    log.error('Failed to fetch runs', sanitizeForLog(err))
    const message = getErrorMessage(err)
    set((state) => ({ errors: { ...state.errors, runs: message } }))
  }
}

export function createFetchActions(set: FineTuningSet) {
  return {
    fetchStatus: () => fetchStatusImpl(set),
    fetchCheckpoints: () => fetchCheckpointsImpl(set),
    fetchRuns: () => fetchRunsImpl(set),
  }
}
