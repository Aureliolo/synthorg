import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { ClipboardCheck } from 'lucide-react'
import { useApprovalsData } from '@/hooks/useApprovalsData'
import { useEmptyStateProps } from '@/hooks/use-empty-state-props'
import { useToastStore } from '@/stores/toast'
import { filterApprovals, groupByRiskLevel, type ApprovalPageFilters } from '@/utils/approvals'
import type { ApprovalRiskLevel } from '@/api/types/enums'
import { REJECTION_REASON_REQUIRED } from './errors'

const VALID_STATUSES: ReadonlySet<string> = new Set(['pending', 'approved', 'rejected', 'expired'])
const VALID_RISK_LEVELS: ReadonlySet<string> = new Set(['critical', 'high', 'medium', 'low'])

function parseFilters(searchParams: URLSearchParams): ApprovalPageFilters {
  const rawStatus = searchParams.get('status')
  const rawRisk = searchParams.get('risk')
  return {
    status: rawStatus && VALID_STATUSES.has(rawStatus) ? (rawStatus as ApprovalPageFilters['status']) : undefined,
    riskLevel: rawRisk && VALID_RISK_LEVELS.has(rawRisk) ? (rawRisk as ApprovalPageFilters['riskLevel']) : undefined,
    actionType: searchParams.get('type') ?? undefined,
    search: searchParams.get('search') ?? undefined,
  }
}

function writeFilters(prev: URLSearchParams, next: ApprovalPageFilters): URLSearchParams {
  const params = new URLSearchParams(prev)
  const sel = params.get('selected')
  for (const key of ['status', 'risk', 'type', 'search']) params.delete(key)
  if (next.status) params.set('status', next.status)
  if (next.riskLevel) params.set('risk', next.riskLevel)
  if (next.actionType) params.set('type', next.actionType)
  if (next.search) params.set('search', next.search)
  if (sel) params.set('selected', sel)
  return params
}

interface ApprovalUrlState {
  filters: ApprovalPageFilters
  selectedId: string | null
  handleFiltersChange: (filters: ApprovalPageFilters) => void
  handleSelectApproval: (id: string) => void
  handleCloseDrawer: () => void
}

function useApprovalUrlState(): ApprovalUrlState {
  const [searchParams, setSearchParams] = useSearchParams()
  const filters = useMemo(() => parseFilters(searchParams), [searchParams])

  const handleFiltersChange = useCallback(
    (next: ApprovalPageFilters) => setSearchParams((prev) => writeFilters(prev, next)),
    [setSearchParams],
  )
  const handleSelectApproval = useCallback(
    (id: string) =>
      setSearchParams((prev) => {
        const params = new URLSearchParams(prev)
        params.set('selected', id)
        return params
      }),
    [setSearchParams],
  )
  const handleCloseDrawer = useCallback(
    () =>
      setSearchParams((prev) => {
        const params = new URLSearchParams(prev)
        params.delete('selected')
        return params
      }),
    [setSearchParams],
  )

  return {
    filters,
    selectedId: searchParams.get('selected'),
    handleFiltersChange,
    handleSelectApproval,
    handleCloseDrawer,
  }
}

type ApprovalsData = ReturnType<typeof useApprovalsData>

export interface ApprovalBatch {
  batchApproveOpen: boolean
  setBatchApproveOpen: (open: boolean) => void
  batchRejectOpen: boolean
  setBatchRejectOpen: (open: boolean) => void
  batchComment: string
  setBatchComment: (value: string) => void
  batchReason: string
  setBatchReason: (value: string) => void
  batchLoading: boolean
  handleBatchApprove: () => Promise<void>
  handleBatchReject: () => Promise<void>
}

function useApprovalBatch(data: ApprovalsData): ApprovalBatch {
  const { selectedIds, batchApprove, batchReject } = data
  const [batchApproveOpen, setBatchApproveOpen] = useState(false)
  const [batchRejectOpen, setBatchRejectOpen] = useState(false)
  const [batchComment, setBatchComment] = useState('')
  const [batchReason, setBatchReason] = useState('')
  const [batchLoading, setBatchLoading] = useState(false)

  // Close batch dialogs when selection empties (WS updates / optimistic transitions).
  const prevSelectionSizeRef = useRef(selectedIds.size)
  useEffect(() => {
    const prevSize = prevSelectionSizeRef.current
    prevSelectionSizeRef.current = selectedIds.size
    if (selectedIds.size === 0 && prevSize > 0) {
      setBatchApproveOpen(false)
      setBatchRejectOpen(false)
      setBatchComment('')
      setBatchReason('')
    }
  }, [selectedIds.size])

  // Store owns batch UX: batch* run allSettled internally and emit the
  // outcome toast themselves, so the page only awaits and resets local state.
  const handleBatchApprove = useCallback(async () => {
    setBatchLoading(true)
    await batchApprove(Array.from(selectedIds), batchComment.trim() || undefined)
    setBatchLoading(false)
    setBatchApproveOpen(false)
    setBatchComment('')
  }, [selectedIds, batchApprove, batchComment])

  const handleBatchReject = useCallback(async () => {
    if (!batchReason.trim()) {
      useToastStore.getState().add({
        variant: 'error',
        title: 'Rejection reason required',
        description: REJECTION_REASON_REQUIRED,
      })
      return
    }
    setBatchLoading(true)
    await batchReject(Array.from(selectedIds), batchReason.trim())
    setBatchLoading(false)
    setBatchRejectOpen(false)
    setBatchReason('')
  }, [selectedIds, batchReject, batchReason])

  return {
    batchApproveOpen,
    setBatchApproveOpen,
    batchRejectOpen,
    setBatchRejectOpen,
    batchComment,
    setBatchComment,
    batchReason,
    setBatchReason,
    batchLoading,
    handleBatchApprove,
    handleBatchReject,
  }
}

function useWasConnected(wsConnected: boolean): React.RefObject<boolean> {
  const wasConnectedRef = useRef(false)
  useEffect(() => {
    if (wsConnected) wasConnectedRef.current = true
  }, [wsConnected])
  return wasConnectedRef
}

export interface ApprovalsDerived {
  filtered: ReturnType<typeof filterApprovals>
  grouped: ReturnType<typeof groupByRiskLevel>
  pendingCount: number
  actionTypes: string[]
  riskCounts: Record<ApprovalRiskLevel, number>
  hasFilters: boolean
}

function useApprovalsDerived(approvals: ApprovalsData['approvals'], filters: ApprovalPageFilters): ApprovalsDerived {
  const filtered = useMemo(() => filterApprovals(approvals, filters), [approvals, filters])
  const grouped = useMemo(() => groupByRiskLevel(filtered), [filtered])
  const pendingCount = useMemo(() => approvals.filter((a) => a.status === 'pending').length, [approvals])
  const actionTypes = useMemo(
    () => [...new Set(approvals.map((a) => a.action_type))].sort(),
    [approvals],
  )
  const riskCounts = useMemo(() => {
    const counts: Record<ApprovalRiskLevel, number> = { critical: 0, high: 0, medium: 0, low: 0 }
    for (const a of approvals) {
      if (a.status === 'pending') counts[a.risk_level]++
    }
    return counts
  }, [approvals])
  const hasFilters = !!(filters.status || filters.riskLevel || filters.actionType || filters.search)
  return { filtered, grouped, pendingCount, actionTypes, riskCounts, hasFilters }
}

export interface ApprovalsPageController {
  data: ApprovalsData
  url: ApprovalUrlState
  batch: ApprovalBatch
  derived: ApprovalsDerived
  wasConnectedRef: React.RefObject<boolean>
  emptyStateProps: ReturnType<typeof useEmptyStateProps>
  handleApproveOne: (id: string) => Promise<void>
  handleRejectOne: (id: string) => void
  handleRiskToggle: (level: ApprovalRiskLevel) => void
}

export function useApprovalsPageController(): ApprovalsPageController {
  const data = useApprovalsData()
  const url = useApprovalUrlState()
  const batch = useApprovalBatch(data)
  const derived = useApprovalsDerived(data.approvals, url.filters)
  const wasConnectedRef = useWasConnected(data.wsConnected)
  const { fetchApproval, approveOne, optimisticApprove } = data
  const { selectedId, filters, handleSelectApproval, handleFiltersChange } = url

  useEffect(() => {
    if (selectedId) void fetchApproval(selectedId)
  }, [fetchApproval, selectedId])

  const handleApproveOne = useCallback(
    async (id: string) => {
      const rollback = optimisticApprove(id)
      const result = await approveOne(id)
      if (!result) rollback()
    },
    [approveOne, optimisticApprove],
  )
  const handleRejectOne = useCallback((id: string) => handleSelectApproval(id), [handleSelectApproval])

  const handleRiskToggle = useCallback(
    (level: ApprovalRiskLevel) => {
      handleFiltersChange({ ...filters, riskLevel: filters.riskLevel === level ? undefined : level })
    },
    [handleFiltersChange, filters],
  )

  const emptyStateProps = useEmptyStateProps({
    filteredCount: derived.filtered.length,
    totalCount: data.approvals.length,
    filterActive: derived.hasFilters,
    icon: ClipboardCheck,
    empty: {
      title: 'No approvals',
      description: "When agents request approval for actions, they'll appear here.",
    },
    filtered: {
      title: 'No matching approvals',
      description: 'Try adjusting your filters.',
      action: { label: 'Clear filters', onClick: () => handleFiltersChange({}) },
    },
  })

  return {
    data,
    url,
    batch,
    derived,
    wasConnectedRef,
    emptyStateProps,
    handleApproveOne,
    handleRejectOne,
    handleRiskToggle,
  }
}
