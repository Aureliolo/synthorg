import { useCallback, useEffect, useMemo } from 'react'
import { useApprovalsStore } from '@/stores/approvals'
import { useWebSocket, type ChannelBinding } from '@/hooks/useWebSocket'
import { usePolling } from '@/hooks/usePolling'
import { useFreshnessGate } from '@/hooks/useFreshnessGate'
import type {
  ApprovalResponse,
  ApproveRequest,
  RejectRequest,
} from '@/api/types/approvals'
import type { WsChannel } from '@/api/types/websocket'

const APPROVAL_FETCH_LIMIT = 200
const APPROVAL_POLL_INTERVAL = 30_000
const APPROVAL_CHANNELS = ['approvals'] as const satisfies readonly WsChannel[]

export interface UseApprovalsDataReturn {
  approvals: ApprovalResponse[]
  selectedApproval: ApprovalResponse | null
  total: number
  loading: boolean
  loadingDetail: boolean
  error: string | null
  detailError: string | null
  isRefetching: boolean
  wsConnected: boolean
  wsSetupError: string | null
  fetchApproval: (id: string) => Promise<void>
  approveOne: (id: string, data?: ApproveRequest) => Promise<ApprovalResponse | null>
  rejectOne: (id: string, data: RejectRequest) => Promise<ApprovalResponse | null>
  optimisticApprove: (id: string) => () => void
  /** Optimistic reject with rollback -- available for consumers that collect reason inline. */
  optimisticReject: (id: string) => () => void
  selectedIds: Set<string>
  toggleSelection: (id: string) => void
  selectAllInGroup: (ids: string[]) => void
  deselectAllInGroup: (ids: string[]) => void
  clearSelection: () => void
  batchApprove: (ids: string[], comment?: string) => Promise<{ succeeded: number; failed: number; failedReasons: string[] }>
  batchReject: (ids: string[], reason: string) => Promise<{ succeeded: number; failed: number; failedReasons: string[] }>
}

export function useApprovalsData(): UseApprovalsDataReturn {
  const approvals = useApprovalsStore((s) => s.approvals)
  const selectedApproval = useApprovalsStore((s) => s.selectedApproval)
  const total = useApprovalsStore((s) => s.total)
  const loading = useApprovalsStore((s) => s.loading)
  const loadingDetail = useApprovalsStore((s) => s.loadingDetail)
  const error = useApprovalsStore((s) => s.error)
  const detailError = useApprovalsStore((s) => s.detailError)
  const fetchApproval = useApprovalsStore((s) => s.fetchApproval)
  const approveOne = useApprovalsStore((s) => s.approveOne)
  const rejectOne = useApprovalsStore((s) => s.rejectOne)
  const optimisticApprove = useApprovalsStore((s) => s.optimisticApprove)
  const optimisticReject = useApprovalsStore((s) => s.optimisticReject)
  const selectedIds = useApprovalsStore((s) => s.selectedIds)
  const toggleSelection = useApprovalsStore((s) => s.toggleSelection)
  const selectAllInGroup = useApprovalsStore((s) => s.selectAllInGroup)
  const deselectAllInGroup = useApprovalsStore((s) => s.deselectAllInGroup)
  const clearSelection = useApprovalsStore((s) => s.clearSelection)
  const batchApprove = useApprovalsStore((s) => s.batchApprove)
  const batchReject = useApprovalsStore((s) => s.batchReject)

  // Initial data fetch -- store owns error UX (list-read pattern: sets error state).
  useEffect(() => {
    void useApprovalsStore.getState().fetchApprovals({ limit: APPROVAL_FETCH_LIMIT })
  }, [])

  // Lightweight polling for approval refresh, gated against fresh WS state.
  const { skipIfFresh, markFresh } = useFreshnessGate()
  const pollFn = useCallback(async () => {
    await useApprovalsStore.getState().fetchApprovals({ limit: APPROVAL_FETCH_LIMIT })
  }, [])
  const polling = usePolling(pollFn, APPROVAL_POLL_INTERVAL, { skipIfFresh })

  const { start, stop } = polling
  useEffect(() => {
    start()
    return () => stop()
  }, [start, stop])

  // WebSocket bindings for real-time updates
  const bindings: ChannelBinding[] = useMemo(
    () =>
      APPROVAL_CHANNELS.map((channel) => ({
        channel,
        handler: (event) => {
          markFresh()
          useApprovalsStore.getState().handleWsEvent(event)
        },
      })),
    [markFresh],
  )

  const { connected: wsConnected, setupError: wsSetupError } = useWebSocket({
    bindings,
  })

  return {
    approvals,
    selectedApproval,
    total,
    loading,
    loadingDetail,
    error,
    detailError,
    isRefetching: polling.isRefetching,
    wsConnected,
    wsSetupError,
    fetchApproval,
    approveOne,
    rejectOne,
    optimisticApprove,
    optimisticReject,
    selectedIds,
    toggleSelection,
    selectAllInGroup,
    deselectAllInGroup,
    clearSelection,
    batchApprove,
    batchReject,
  }
}
