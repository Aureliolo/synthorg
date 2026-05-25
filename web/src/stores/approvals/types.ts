import type { StoreApi } from 'zustand'
import type {
  ApprovalFilters,
  ApprovalResponse,
  ApproveRequest,
  RejectRequest,
} from '@/api/types/approvals'
import type { WsEvent } from '@/api/types/websocket'

export interface ApprovalsState {
  // Data
  approvals: ApprovalResponse[]
  selectedApproval: ApprovalResponse | null
  total: number

  // Loading
  loading: boolean
  loadingDetail: boolean
  error: string | null
  detailError: string | null

  // CRUD
  fetchApprovals: (filters?: ApprovalFilters) => Promise<void>
  fetchApproval: (id: string) => Promise<void>
  approveOne: (
    id: string,
    data?: ApproveRequest,
  ) => Promise<ApprovalResponse | null>
  rejectOne: (
    id: string,
    data: RejectRequest,
  ) => Promise<ApprovalResponse | null>

  // Real-time
  handleWsEvent: (event: WsEvent) => void

  // Optimistic helpers
  pendingTransitions: Set<string>
  optimisticApprove: (id: string) => () => void
  optimisticReject: (id: string) => () => void
  upsertApproval: (approval: ApprovalResponse) => void

  // Batch selection
  selectedIds: Set<string>
  toggleSelection: (id: string) => void
  selectAllInGroup: (ids: string[]) => void
  deselectAllInGroup: (ids: string[]) => void
  clearSelection: () => void

  // Batch operations
  batchApprove: (
    ids: string[],
    comment?: string,
  ) => Promise<{ succeeded: number; failed: number; failedReasons: string[] }>
  batchReject: (
    ids: string[],
    reason: string,
  ) => Promise<{ succeeded: number; failed: number; failedReasons: string[] }>

  // Lifecycle (#1600 Phase 5). No-op today; future timers / listeners
  // should be torn down here so the global ``afterEach`` in
  // ``web/src/test-setup.tsx`` releases them deterministically.
  dispose: () => void
}

export type ApprovalsSet = StoreApi<ApprovalsState>['setState']
export type ApprovalsGet = StoreApi<ApprovalsState>['getState']
