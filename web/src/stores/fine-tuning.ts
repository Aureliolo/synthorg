import { create } from 'zustand'

import {
  cancelFineTune,
  deleteCheckpoint,
  deployCheckpoint,
  getFineTuneStatus,
  listCheckpoints,
  listRuns,
  rollbackCheckpoint,
  runPreflight,
  startFineTune,
} from '@/api/endpoints/fine-tuning'
import type {
  CheckpointRecord,
  FineTuneRun,
  FineTuneStage,
  FineTuneStatus,
  PreflightResult,
  StartFineTuneRequest,
} from '@/api/endpoints/fine-tuning'
import type { WsEvent } from '@/api/types/websocket'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'

/** All valid fine-tune stage values for runtime validation of WS payloads. */
const VALID_STAGES: ReadonlySet<string> = new Set<FineTuneStage>([
  'idle', 'generating_data', 'mining_negatives', 'training',
  'evaluating', 'deploying', 'complete', 'failed',
])

const log = createLogger('fine-tuning-store')

/** Per-resource error map so a successful fetch never clears another's failure. */
export interface FineTuningErrors {
  status: string | null
  checkpoints: string | null
  runs: string | null
}

const NO_ERRORS: FineTuningErrors = {
  status: null,
  checkpoints: null,
  runs: null,
}

// Per-list pagination state surfaced from the wire envelope. Both
// fields default to false/null on every fetch path; the dashboard
// drains every page on initial fetch so an exhausted list always
// settles at ``hasMore=false, nextCursor=null``. Stays in state so
// future load-more UI can branch on a single flag (per the cursor
// pagination convention in ``web/CLAUDE.md``).
interface ListPagination {
  nextCursor: string | null
  hasMore: boolean
}

const NO_MORE: ListPagination = { nextCursor: null, hasMore: false }

// Page size used when draining ``listCheckpoints`` / ``listRuns``.
// Matches the endpoint default; smaller pages only multiply
// round-trips without changing the draining outcome.
const LIST_PAGE_SIZE = 50

// Safety stop so a backend bug that keeps returning ``has_more=true``
// cannot lock the dashboard in an infinite drain loop. Sized to cover
// the bounded admin lists (checkpoints / runs) with a generous margin.
const DRAIN_PAGE_LIMIT = 50

interface FineTuningState {
  // State
  status: FineTuneStatus | null
  checkpoints: readonly CheckpointRecord[]
  checkpointsPagination: ListPagination
  runs: readonly FineTuneRun[]
  runsPagination: ListPagination
  preflight: PreflightResult | null
  loading: boolean
  errors: FineTuningErrors

  // Actions
  fetchStatus: () => Promise<void>
  fetchCheckpoints: () => Promise<void>
  fetchRuns: () => Promise<void>
  startRun: (request: StartFineTuneRequest) => Promise<void>
  cancelRun: () => Promise<void>
  runPreflightCheck: (request: StartFineTuneRequest) => Promise<void>
  deployCheckpointAction: (id: string) => Promise<void>
  rollbackCheckpointAction: (id: string) => Promise<void>
  deleteCheckpointAction: (id: string) => Promise<void>
  handleWsEvent: (event: WsEvent) => void
}

/**
 * Pick the first non-null error from the per-resource map for a single
 * banner string. When several resources fail concurrently the page surfaces
 * status > checkpoints > runs in priority order; per-resource detail is
 * still available on ``state.errors`` for finer-grained UI.
 */
export function selectFineTuningBannerError(
  errors: FineTuningErrors,
): string | null {
  return errors.status ?? errors.checkpoints ?? errors.runs ?? null
}

export const useFineTuningStore = create<FineTuningState>((set, get) => ({
  status: null,
  checkpoints: [],
  checkpointsPagination: NO_MORE,
  runs: [],
  runsPagination: NO_MORE,
  preflight: null,
  loading: false,
  errors: NO_ERRORS,

  // Fetch actions follow web/CLAUDE.md: track error per resource so a
  // later successful fetch never clears another's failure (the
  // FineTuningPage bootstrap fans these out concurrently). The page
  // surfaces a single banner string via ``selectFineTuningBannerError``.
  // We intentionally do not toast on fetch failures; the inline banner
  // already covers user awareness.
  fetchStatus: async () => {
    try {
      const status = await getFineTuneStatus()
      set((state) => ({ status, errors: { ...state.errors, status: null } }))
    } catch (err) {
      log.error('Failed to fetch fine-tune status', sanitizeForLog(err))
      const message = getErrorMessage(err)
      set((state) => ({ errors: { ...state.errors, status: message } }))
    }
  },

  fetchCheckpoints: async () => {
    try {
      const collected: CheckpointRecord[] = []
      let lastPagination: ListPagination = NO_MORE
      let cursor: string | null = null
      for (let i = 0; i < DRAIN_PAGE_LIMIT; i++) {
        const page = await listCheckpoints(cursor, LIST_PAGE_SIZE)
        collected.push(...page.data)
        lastPagination = { nextCursor: page.nextCursor, hasMore: page.hasMore }
        if (!page.hasMore || !page.nextCursor) break
        cursor = page.nextCursor
      }
      set((state) => ({
        checkpoints: collected,
        checkpointsPagination: lastPagination,
        errors: { ...state.errors, checkpoints: null },
      }))
    } catch (err) {
      log.error('Failed to fetch checkpoints', sanitizeForLog(err))
      const message = getErrorMessage(err)
      set((state) => ({ errors: { ...state.errors, checkpoints: message } }))
    }
  },

  fetchRuns: async () => {
    try {
      const collected: FineTuneRun[] = []
      let lastPagination: ListPagination = NO_MORE
      let cursor: string | null = null
      for (let i = 0; i < DRAIN_PAGE_LIMIT; i++) {
        const page = await listRuns(cursor, LIST_PAGE_SIZE)
        collected.push(...page.data)
        lastPagination = { nextCursor: page.nextCursor, hasMore: page.hasMore }
        if (!page.hasMore || !page.nextCursor) break
        cursor = page.nextCursor
      }
      set((state) => ({
        runs: collected,
        runsPagination: lastPagination,
        errors: { ...state.errors, runs: null },
      }))
    } catch (err) {
      log.error('Failed to fetch runs', sanitizeForLog(err))
      const message = getErrorMessage(err)
      set((state) => ({ errors: { ...state.errors, runs: message } }))
    }
  },

  // Mutations follow the canonical store pattern (web/CLAUDE.md "Zustand
  // Store Error Handling"): log + error toast on failure. The store
  // ``error`` field is reserved for fetch failures so the page-level
  // ErrorBanner shows fetch errors only; mutation toasts are the sole
  // user-facing surface for mutation errors. Success paths stay silent;
  // the resulting state change is its own confirmation here.
  startRun: async (request) => {
    set({ loading: true })
    try {
      const status = await startFineTune(request)
      set({ status, loading: false })
    } catch (err) {
      log.error('Failed to start fine-tune run', sanitizeForLog(err))
      set({ loading: false })
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Failed to start fine-tune run'),
        description: getErrorMessage(err),
      })
    }
  },

  cancelRun: async () => {
    try {
      const status = await cancelFineTune()
      set({ status })
    } catch (err) {
      log.error('Failed to cancel fine-tune run', sanitizeForLog(err))
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Failed to cancel fine-tune run'),
        description: getErrorMessage(err),
      })
    }
  },

  runPreflightCheck: async (request) => {
    set({ loading: true, preflight: null })
    try {
      const result = await runPreflight(request)
      set({ preflight: result, loading: false })
    } catch (err) {
      log.error('Failed to run preflight', sanitizeForLog(err))
      set({ loading: false })
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Preflight check failed'),
        description: getErrorMessage(err),
      })
    }
  },

  deployCheckpointAction: async (id) => {
    try {
      await deployCheckpoint(id)
      await get().fetchCheckpoints()
    } catch (err) {
      log.error('Failed to deploy checkpoint', sanitizeForLog(err))
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Failed to deploy checkpoint'),
        description: getErrorMessage(err),
      })
    }
  },

  rollbackCheckpointAction: async (id) => {
    try {
      await rollbackCheckpoint(id)
      await get().fetchCheckpoints()
    } catch (err) {
      log.error('Failed to rollback checkpoint', sanitizeForLog(err))
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Failed to rollback checkpoint'),
        description: getErrorMessage(err),
      })
    }
  },

  deleteCheckpointAction: async (id) => {
    try {
      await deleteCheckpoint(id)
      await get().fetchCheckpoints()
    } catch (err) {
      log.error('Failed to delete checkpoint', sanitizeForLog(err))
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Failed to delete checkpoint'),
        description: getErrorMessage(err),
      })
    }
  },

  handleWsEvent: (event) => {
    const { event_type: eventType, payload: data } = event
    if (!eventType.startsWith('memory.fine_tune.')) return

    const currentStatus = get().status

    const rawStage = data.stage as string | undefined
    const stage: FineTuneStatus['stage'] =
      rawStage != null && VALID_STAGES.has(rawStage)
        ? (rawStage as FineTuneStatus['stage'])
        : (currentStatus?.stage ?? 'idle')

    const rawProgress = data.progress as number | undefined
    const progress =
      rawProgress != null ? Math.min(1, Math.max(0, rawProgress)) : null

    if (eventType === 'memory.fine_tune.progress') {
      set({
        status: {
          run_id: (data.run_id as string) ?? currentStatus?.run_id ?? null,
          stage,
          progress,
          error: null,
        },
      })
    } else if (eventType === 'memory.fine_tune.stage_changed') {
      set({
        status: {
          run_id: (data.run_id as string) ?? currentStatus?.run_id ?? null,
          stage,
          progress: 0,
          error: null,
        },
      })
    } else if (
      eventType === 'memory.fine_tune.completed' ||
      eventType === 'memory.fine_tune.failed'
    ) {
      // Refresh all data on completion/failure.
      void get().fetchStatus()
      void get().fetchCheckpoints()
      void get().fetchRuns()
    }
  },
}))
