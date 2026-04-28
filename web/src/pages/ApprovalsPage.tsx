import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { AnimatePresence } from 'motion/react'
import { ClipboardCheck } from 'lucide-react'
import { MetricCard } from '@/components/ui/metric-card'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { InputField } from '@/components/ui/input-field'
import { ListHeader } from '@/components/ui/list-header'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { useApprovalsData } from '@/hooks/useApprovalsData'
import { useToastStore } from '@/stores/toast'
import {
  filterApprovals,
  groupByRiskLevel,
  type ApprovalPageFilters,
} from '@/utils/approvals'
import { formatNumber } from '@/utils/format'

import { ApprovalFilterBar } from './approvals/ApprovalFilterBar'
import { ApprovalRiskGroupSection } from './approvals/ApprovalRiskGroupSection'
import { ApprovalDetailDrawer } from './approvals/ApprovalDetailDrawer'
import { BatchActionBar } from './approvals/BatchActionBar'
import { ApprovalsSkeleton } from './approvals/ApprovalsSkeleton'
import type { ApprovalRiskLevel } from '@/api/types/enums'

const VALID_STATUSES: ReadonlySet<string> = new Set(['pending', 'approved', 'rejected', 'expired'])
const VALID_RISK_LEVELS: ReadonlySet<string> = new Set(['critical', 'high', 'medium', 'low'])

export default function ApprovalsPage() {
  const {
    approvals,
    selectedApproval,
    loading,
    loadingDetail,
    error,
    wsConnected,
    wsSetupError,
    fetchApproval,
    approveOne,
    rejectOne,
    optimisticApprove,
    selectedIds,
    toggleSelection,
    selectAllInGroup,
    deselectAllInGroup,
    clearSelection,
    batchApprove,
    batchReject,
    detailError,
  } = useApprovalsData()

  const [searchParams, setSearchParams] = useSearchParams()
  const [batchApproveOpen, setBatchApproveOpen] = useState(false)
  const [batchRejectOpen, setBatchRejectOpen] = useState(false)
  const [batchComment, setBatchComment] = useState('')
  const [batchReason, setBatchReason] = useState('')
  const [batchLoading, setBatchLoading] = useState(false)
  const [wasConnected, setWasConnected] = useState(false)

  // Track whether WS was ever connected to avoid flash on initial load
  if (wsConnected && !wasConnected) {
    setWasConnected(true)
  }

  // URL-synced filters
  const filters: ApprovalPageFilters = useMemo(() => {
    const rawStatus = searchParams.get('status')
    const rawRisk = searchParams.get('risk')
    return {
      status: rawStatus && VALID_STATUSES.has(rawStatus) ? rawStatus as ApprovalPageFilters['status'] : undefined,
      riskLevel: rawRisk && VALID_RISK_LEVELS.has(rawRisk) ? rawRisk as ApprovalPageFilters['riskLevel'] : undefined,
      actionType: searchParams.get('type') ?? undefined,
      search: searchParams.get('search') ?? undefined,
    }
  }, [searchParams])

  const selectedId = searchParams.get('selected')

  const handleFiltersChange = useCallback((newFilters: ApprovalPageFilters) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      // Replace filter params while preserving the 'selected' param for drawer state
      const sel = next.get('selected')
      // Clear old filter params
      next.delete('status')
      next.delete('risk')
      next.delete('type')
      next.delete('search')
      // Set new ones
      if (newFilters.status) next.set('status', newFilters.status)
      if (newFilters.riskLevel) next.set('risk', newFilters.riskLevel)
      if (newFilters.actionType) next.set('type', newFilters.actionType)
      if (newFilters.search) next.set('search', newFilters.search)
      if (sel) next.set('selected', sel)
      return next
    })
  }, [setSearchParams])

  const handleSelectApproval = useCallback((approvalId: string) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      next.set('selected', approvalId)
      return next
    })
  }, [setSearchParams])

  const handleCloseDrawer = useCallback(() => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      next.delete('selected')
      return next
    })
  }, [setSearchParams])

  // Fetch approval detail when URL selected param changes
  useEffect(() => {
    if (selectedId) {
      fetchApproval(selectedId)
    }
  }, [fetchApproval, selectedId])

  // Single item approve -- optimistic update with rollback on failure
  const handleApproveOne = useCallback(async (id: string) => {
    const rollback = optimisticApprove(id)
    const result = await approveOne(id)
    if (!result) rollback()
  }, [approveOne, optimisticApprove])

  // Single item reject -- opens drawer for the user to provide a reason
  const handleRejectOne = useCallback(async (id: string) => {
    // For single reject, open the drawer so user can enter reason
    handleSelectApproval(id)
  }, [handleSelectApproval])

  // Close batch dialogs when selection is emptied (e.g., by WS updates or optimistic transitions)
  const prevSelectionSizeRef = useRef(selectedIds.size)
  if (selectedIds.size === 0 && prevSelectionSizeRef.current > 0) {
    setBatchApproveOpen(false)
    setBatchRejectOpen(false)
    setBatchComment('')
    setBatchReason('')
  }
  prevSelectionSizeRef.current = selectedIds.size

  // Batch actions
  //
  // The store owns error UX per ``web/CLAUDE.md`` "Zustand Store Error
  // Handling": ``batchApprove`` / ``batchReject`` use ``allSettled``
  // internally and always resolve to a structured ``{ succeeded,
  // failed, failedReasons }`` result.  Per-item failures land in the
  // result; the page just renders the right toast variant from the
  // counts.  No try/catch wraps the store call (audit 38 + 58).
  const handleBatchApprove = useCallback(async () => {
    setBatchLoading(true)
    const ids = Array.from(selectedIds)
    const result = await batchApprove(ids, batchComment.trim() || undefined)
    setBatchLoading(false)
    setBatchApproveOpen(false)
    setBatchComment('')
    if (result.failed === 0) {
      useToastStore.getState().add({
        variant: 'success',
        title: `Approved ${result.succeeded} items`,
      })
    } else {
      useToastStore.getState().add({
        variant: 'warning',
        title: `Approved ${result.succeeded} of ${ids.length}. ${result.failed} failed.`,
        description:
          result.failedReasons.length > 0 ? result.failedReasons.join('; ') : undefined,
      })
    }
  }, [selectedIds, batchApprove, batchComment])

  const handleBatchReject = useCallback(async () => {
    if (!batchReason.trim()) {
      useToastStore.getState().add({
        variant: 'error',
        title: 'Please provide a rejection reason',
      })
      return
    }
    setBatchLoading(true)
    const ids = Array.from(selectedIds)
    const result = await batchReject(ids, batchReason.trim())
    setBatchLoading(false)
    setBatchRejectOpen(false)
    setBatchReason('')
    if (result.failed === 0) {
      useToastStore.getState().add({
        variant: 'success',
        title: `Rejected ${result.succeeded} items`,
      })
    } else {
      useToastStore.getState().add({
        variant: 'warning',
        title: `Rejected ${result.succeeded} of ${ids.length}. ${result.failed} failed.`,
        description:
          result.failedReasons.length > 0 ? result.failedReasons.join('; ') : undefined,
      })
    }
  }, [selectedIds, batchReject, batchReason])

  // Derived data
  const filtered = useMemo(() => filterApprovals(approvals, filters), [approvals, filters])
  const grouped = useMemo(() => groupByRiskLevel(filtered), [filtered])
  const pendingCount = useMemo(() => approvals.filter((a) => a.status === 'pending').length, [approvals])

  const actionTypes = useMemo(
    () => [...new Set(approvals.map((a) => a.action_type))].sort(),
    [approvals],
  )

  // Metric cards for pending counts by risk level
  const riskCounts = useMemo(() => {
    const counts: Record<ApprovalRiskLevel, number> = { critical: 0, high: 0, medium: 0, low: 0 }
    for (const a of approvals) {
      if (a.status === 'pending') counts[a.risk_level]++
    }
    return counts
  }, [approvals])

  // Loading state
  if (loading && approvals.length === 0) {
    return <ApprovalsSkeleton />
  }

  const hasFilters = !!(filters.status || filters.riskLevel || filters.actionType || filters.search)

  return (
    <div className="space-y-section-gap">
      <ListHeader
        title="Approvals"
        count={filtered.length}
        countLabel={
          filtered.length === approvals.length
            ? undefined
            : `${formatNumber(filtered.length)} of ${formatNumber(approvals.length)}`
        }
      />

      {error && (
        <ErrorBanner severity="error" title="Could not load approvals" description={error} />
      )}

      {(wsSetupError || (wasConnected && !wsConnected)) && !loading && (
        <ErrorBanner
          variant="offline"
          title="Real-time updates disconnected"
          description={wsSetupError ?? 'Data may be stale until the connection recovers.'}
        />
      )}

      <ApprovalFilterBar
        filters={filters}
        onFiltersChange={handleFiltersChange}
        pendingCount={pendingCount}
        totalCount={approvals.length}
        actionTypes={actionTypes}
      />

      {/* Pending counts by risk level */}
      <StaggerGroup className="grid grid-cols-4 gap-grid-gap max-[1023px]:grid-cols-2">
        <StaggerItem>
          <MetricCard label="Critical" value={riskCounts.critical} className="border-l-2 border-l-danger" />
        </StaggerItem>
        <StaggerItem>
          <MetricCard label="High" value={riskCounts.high} className="border-l-2 border-l-warning" />
        </StaggerItem>
        <StaggerItem>
          <MetricCard label="Medium" value={riskCounts.medium} className="border-l-2 border-l-accent" />
        </StaggerItem>
        <StaggerItem>
          <MetricCard label="Low" value={riskCounts.low} className="border-l-2 border-l-accent-dim" />
        </StaggerItem>
      </StaggerGroup>

      {/* Risk-grouped sections */}
      {grouped.size === 0 && !hasFilters && (
        <EmptyState
          icon={ClipboardCheck}
          title="No approvals"
          description="When agents request approval for actions, they'll appear here."
        />
      )}

      {grouped.size === 0 && hasFilters && (
        <EmptyState
          icon={ClipboardCheck}
          title="No matching approvals"
          description="Try adjusting your filters."
          action={{ label: 'Clear filters', onClick: () => handleFiltersChange({}) }}
        />
      )}

      {[...grouped.entries()].map(([riskLevel, items]) => (
        <ApprovalRiskGroupSection
          key={riskLevel}
          riskLevel={riskLevel}
          items={items}
          selectedIds={selectedIds}
          onSelectAll={selectAllInGroup}
          onDeselectAll={deselectAllInGroup}
          onToggleSelect={toggleSelection}
          onSelect={handleSelectApproval}
          onApprove={handleApproveOne}
          onReject={handleRejectOne}
        />
      ))}

      {/* Detail drawer */}
      <AnimatePresence>
        {!!selectedId && (
          <ApprovalDetailDrawer
            approval={selectedApproval}
            open={!!selectedId}
            onClose={handleCloseDrawer}
            onApprove={async (id, data) => {
              const result = await approveOne(id, data)
              if (result) handleCloseDrawer()
              return result !== null
            }}
            onReject={async (id, data) => {
              const result = await rejectOne(id, data)
              if (result) handleCloseDrawer()
              return result !== null
            }}
            loading={loadingDetail}
            error={detailError}
          />
        )}
      </AnimatePresence>

      {/* Batch action bar */}
      <AnimatePresence>
        {selectedIds.size > 0 && (
          <BatchActionBar
            selectedCount={selectedIds.size}
            onApproveAll={() => setBatchApproveOpen(true)}
            onRejectAll={() => setBatchRejectOpen(true)}
            onClearSelection={clearSelection}
            loading={batchLoading}
          />
        )}
      </AnimatePresence>

      {/* Batch approve dialog */}
      <ConfirmDialog
        open={batchApproveOpen}
        onOpenChange={(o) => { setBatchApproveOpen(o); if (!o) setBatchComment('') }}
        title={`Approve ${formatNumber(selectedIds.size)} approval${selectedIds.size === 1 ? '' : 's'}?`}
        description="This will approve every selected pending item. Agents will resume work using the approved parameters."
        confirmLabel={`Approve ${formatNumber(selectedIds.size)}`}
        onConfirm={handleBatchApprove}
        loading={batchLoading}
      >
        <InputField
          multiline
          label="Optional comment"
          value={batchComment}
          onValueChange={setBatchComment}
          placeholder="Add context that applies to every approved item..."
          rows={3}
          maxLength={2000}
          className="mt-2"
        />
      </ConfirmDialog>

      {/* Batch reject dialog */}
      <ConfirmDialog
        open={batchRejectOpen}
        onOpenChange={(o) => { setBatchRejectOpen(o); if (!o) setBatchReason('') }}
        title={`Reject ${formatNumber(selectedIds.size)} approval${selectedIds.size === 1 ? '' : 's'}?`}
        description="This will reject every selected pending item. The requester will see the reason below. This action cannot be undone."
        confirmLabel={`Reject ${formatNumber(selectedIds.size)}`}
        variant="destructive"
        onConfirm={handleBatchReject}
        loading={batchLoading}
      >
        <InputField
          multiline
          label="Reason for rejection"
          value={batchReason}
          onValueChange={setBatchReason}
          placeholder="Give the requester enough context to iterate."
          rows={3}
          maxLength={2000}
          required
          autoFocus
          className="mt-2"
        />
      </ConfirmDialog>
    </div>
  )
}
