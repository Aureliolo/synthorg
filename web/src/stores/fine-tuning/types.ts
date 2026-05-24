import type { StoreApi } from 'zustand'
import type {
  CheckpointRecord,
  FineTuneRun,
  FineTuneStatus,
  PreflightResult,
  StartFineTuneRequest,
} from '@/api/endpoints/fine-tuning'
import type { WsEvent } from '@/api/types/websocket'

/** Per-resource error map so a successful fetch never clears another's failure. */
export interface FineTuningErrors {
  status: string | null
  checkpoints: string | null
  runs: string | null
}

// Per-list pagination state surfaced from the wire envelope. Both
// fields default to false/null on every fetch path; the dashboard
// drains every page on initial fetch so an exhausted list always
// settles at ``hasMore=false, nextCursor=null``.
export interface ListPagination {
  nextCursor: string | null
  hasMore: boolean
}

export interface FineTuningState {
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

export type FineTuningSet = StoreApi<FineTuningState>['setState']
export type FineTuningGet = StoreApi<FineTuningState>['getState']
