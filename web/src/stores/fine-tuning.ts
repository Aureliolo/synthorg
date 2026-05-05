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

/** All valid fine-tune stage values for runtime validation of WS payloads. */
const VALID_STAGES: ReadonlySet<string> = new Set<FineTuneStage>([
  'idle', 'generating_data', 'mining_negatives', 'training',
  'evaluating', 'deploying', 'complete', 'failed',
])

const log = createLogger('fine-tuning-store')

interface FineTuningState {
  // State
  status: FineTuneStatus | null
  checkpoints: readonly CheckpointRecord[]
  runs: readonly FineTuneRun[]
  preflight: PreflightResult | null
  loading: boolean
  error: string | null

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

export const useFineTuningStore = create<FineTuningState>((set, get) => ({
  status: null,
  checkpoints: [],
  runs: [],
  preflight: null,
  loading: false,
  error: null,

  // Fetch actions follow web/CLAUDE.md: set error: string | null on the
  // store; the FineTuningPage renders an ErrorBanner from this state. We
  // intentionally do not toast on fetch failures (toasts are reserved for
  // mutations); the inline banner already covers user awareness.
  fetchStatus: async () => {
    try {
      const status = await getFineTuneStatus()
      set({ status, error: null })
    } catch (err) {
      log.error('Failed to fetch fine-tune status', err)
      set({ error: getErrorMessage(err) })
    }
  },

  fetchCheckpoints: async () => {
    try {
      const checkpoints = await listCheckpoints()
      set({ checkpoints, error: null })
    } catch (err) {
      log.error('Failed to fetch checkpoints', err)
      set({ error: getErrorMessage(err) })
    }
  },

  fetchRuns: async () => {
    try {
      const runs = await listRuns()
      set({ runs, error: null })
    } catch (err) {
      log.error('Failed to fetch runs', err)
      set({ error: getErrorMessage(err) })
    }
  },

  // Mutations follow the canonical store pattern: log + error toast on
  // failure (success path is silent; the resulting state change is its
  // own confirmation here).
  startRun: async (request) => {
    set({ loading: true, error: null })
    try {
      const status = await startFineTune(request)
      set({ status, loading: false })
    } catch (err) {
      log.error('Failed to start fine-tune run', err)
      set({ loading: false, error: getErrorMessage(err) })
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
      set({ status, error: null })
    } catch (err) {
      log.error('Failed to cancel fine-tune run', err)
      set({ error: getErrorMessage(err) })
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Failed to cancel fine-tune run'),
        description: getErrorMessage(err),
      })
    }
  },

  runPreflightCheck: async (request) => {
    set({ loading: true, preflight: null, error: null })
    try {
      const result = await runPreflight(request)
      set({ preflight: result, loading: false, error: null })
    } catch (err) {
      log.error('Failed to run preflight', err)
      set({ loading: false, error: getErrorMessage(err) })
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
      log.error('Failed to deploy checkpoint', err)
      set({ error: getErrorMessage(err) })
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
      log.error('Failed to rollback checkpoint', err)
      set({ error: getErrorMessage(err) })
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
      log.error('Failed to delete checkpoint', err)
      set({ error: getErrorMessage(err) })
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
