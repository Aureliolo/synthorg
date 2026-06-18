import { AnimatePresence } from 'motion/react'
import { cn } from '@/lib/utils'
import { MetricCard } from '@/components/ui/metric-card'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { InputField } from '@/components/ui/input-field'
import { ListHeader } from '@/components/ui/list-header'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { formatNumber } from '@/utils/format'
import type { ApprovalRiskLevel } from '@/api/types/enums'
import { ApprovalFilterBar } from './approvals/ApprovalFilterBar'
import { ApprovalRiskGroupSection } from './approvals/ApprovalRiskGroupSection'
import { ApprovalDetailDrawer } from './approvals/ApprovalDetailDrawer'
import { BatchActionBar } from './approvals/BatchActionBar'
import { ApprovalsSkeleton } from './approvals/ApprovalsSkeleton'
import {
  type ApprovalsPageController,
  useApprovalsPageController,
} from './approvals/useApprovalsPage'

const RISK_CARDS: { label: string; riskLevel: ApprovalRiskLevel; className: string }[] = [
  { label: 'Critical', riskLevel: 'critical', className: 'border-l-2 border-l-danger' },
  { label: 'High', riskLevel: 'high', className: 'border-l-2 border-l-warning' },
  { label: 'Medium', riskLevel: 'medium', className: 'border-l-2 border-l-accent' },
  { label: 'Low', riskLevel: 'low', className: 'border-l-2 border-l-accent-dim' },
]

interface RiskFilterMetricCardProps {
  label: string
  value: number
  riskLevel: ApprovalRiskLevel
  active: boolean
  onToggle: (level: ApprovalRiskLevel) => void
  className?: string
}

function RiskFilterMetricCard({ label, value, riskLevel, active, onToggle, className }: RiskFilterMetricCardProps) {
  return (
    <button
      type="button"
      onClick={() => onToggle(riskLevel)}
      aria-pressed={active}
      aria-label={`Filter by ${label.toLowerCase()} risk (${value} pending)`}
      className={cn(
        'block w-full text-left rounded-lg transition-all duration-200 hover:-translate-y-px hover:shadow-[var(--so-shadow-card-hover)]',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
        active && 'ring-2 ring-accent',
      )}
    >
      <MetricCard label={label} value={value} className={className} />
    </button>
  )
}

function RiskMetricCards({
  riskCounts,
  activeFilter,
  onToggle,
}: {
  riskCounts: Record<ApprovalRiskLevel, number>
  activeFilter: ApprovalRiskLevel | undefined
  onToggle: (level: ApprovalRiskLevel) => void
}) {
  return (
    <StaggerGroup className="grid grid-cols-4 gap-grid-gap max-[1023px]:grid-cols-2">
      {RISK_CARDS.map((card) => (
        <StaggerItem key={card.riskLevel}>
          <RiskFilterMetricCard
            label={card.label}
            value={riskCounts[card.riskLevel]}
            riskLevel={card.riskLevel}
            active={activeFilter === card.riskLevel}
            onToggle={onToggle}
            className={card.className}
          />
        </StaggerItem>
      ))}
    </StaggerGroup>
  )
}

function ApprovalsBanners({
  error,
  wsSetupError,
  wsConnected,
  wasConnected,
  loading,
}: {
  error: string | null
  wsSetupError: string | null
  wsConnected: boolean
  wasConnected: boolean
  loading: boolean
}) {
  const wsOffline = Boolean((wsSetupError || (wasConnected && !wsConnected)) && !loading)
  return (
    <>
      {error && <ErrorBanner severity="error" title="Could not load approvals" description={error} />}
      {wsOffline && (
        <ErrorBanner
          variant="offline"
          title="Real-time updates disconnected"
          description={wsSetupError ?? 'Data may be stale until the connection recovers.'}
        />
      )}
    </>
  )
}

function ApprovalGroups({ ctrl }: { ctrl: ApprovalsPageController }) {
  const { data, url } = ctrl
  return (
    <>
      {[...ctrl.derived.grouped.entries()].map(([riskLevel, items]) => (
        <ApprovalRiskGroupSection
          key={riskLevel}
          riskLevel={riskLevel}
          items={items}
          selectedIds={data.selectedIds}
          onSelectAll={data.selectAllInGroup}
          onDeselectAll={data.deselectAllInGroup}
          onToggleSelect={data.toggleSelection}
          onSelect={url.handleSelectApproval}
          onApprove={(id) => void ctrl.handleApproveOne(id)}
          onReject={(id) => ctrl.handleRejectOne(id)}
        />
      ))}
    </>
  )
}

function ApprovalDrawerHost({ ctrl }: { ctrl: ApprovalsPageController }) {
  const { data, url } = ctrl
  return (
    <AnimatePresence>
      {!!url.selectedId && (
        <ApprovalDetailDrawer
          approval={data.selectedApproval}
          open={!!url.selectedId}
          onClose={url.handleCloseDrawer}
          onApprove={async (id, payload) => {
            const result = await data.approveOne(id, payload)
            if (result) url.handleCloseDrawer()
            return result !== null
          }}
          onReject={async (id, payload) => {
            const result = await data.rejectOne(id, payload)
            if (result) url.handleCloseDrawer()
            return result !== null
          }}
          loading={data.loadingDetail}
          error={data.detailError}
        />
      )}
    </AnimatePresence>
  )
}

function ApprovalBatchSection({ ctrl }: { ctrl: ApprovalsPageController }) {
  const { batch, data } = ctrl
  const count = data.selectedIds.size
  const plural = count === 1 ? '' : 's'
  return (
    <>
      <AnimatePresence>
        {count > 0 && (
          <BatchActionBar
            selectedCount={count}
            onApproveAll={() => batch.setBatchApproveOpen(true)}
            onRejectAll={() => batch.setBatchRejectOpen(true)}
            onClearSelection={data.clearSelection}
            loading={batch.batchLoading}
          />
        )}
      </AnimatePresence>

      <ConfirmDialog
        open={batch.batchApproveOpen}
        onOpenChange={(o) => {
          batch.setBatchApproveOpen(o)
          if (!o) batch.setBatchComment('')
        }}
        title={`Approve ${formatNumber(count)} approval${plural}?`}
        description="This will approve every selected pending item. Agents will resume work using the approved parameters."
        confirmLabel={`Approve ${formatNumber(count)}`}
        onConfirm={batch.handleBatchApprove}
        loading={batch.batchLoading}
      >
        <InputField
          multiline
          label="Optional comment"
          value={batch.batchComment}
          onValueChange={batch.setBatchComment}
          placeholder="Add context that applies to every approved item..."
          rows={3}
          maxLength={2000}
          className="mt-2"
        />
      </ConfirmDialog>

      <ConfirmDialog
        open={batch.batchRejectOpen}
        onOpenChange={(o) => {
          batch.setBatchRejectOpen(o)
          if (!o) batch.setBatchReason('')
        }}
        title={`Reject ${formatNumber(count)} approval${plural}?`}
        description="This will reject every selected pending item. The requester will see the reason below. This action cannot be undone."
        confirmLabel={`Reject ${formatNumber(count)}`}
        variant="destructive"
        onConfirm={batch.handleBatchReject}
        loading={batch.batchLoading}
      >
        <InputField
          multiline
          label="Reason for rejection"
          value={batch.batchReason}
          onValueChange={batch.setBatchReason}
          placeholder="Give the requester enough context to iterate."
          rows={3}
          maxLength={2000}
          required
          autoFocus
          className="mt-2"
        />
      </ConfirmDialog>
    </>
  )
}

export default function ApprovalsPage() {
  const ctrl = useApprovalsPageController()
  const { data, url, derived } = ctrl

  if (data.loading && data.approvals.length === 0) {
    return <ApprovalsSkeleton />
  }

  const total = data.approvals.length

  return (
    <div className="space-y-section-gap">
      <ListHeader
        title="Approvals"
        count={derived.filtered.length}
        countLabel={
          derived.filtered.length === total
            ? undefined
            : `${formatNumber(derived.filtered.length)} of ${formatNumber(total)}`
        }
        refreshing={data.isRefetching}
      />

      <ApprovalsBanners
        error={data.error}
        wsSetupError={data.wsSetupError}
        wsConnected={data.wsConnected}
        wasConnected={ctrl.wasConnectedRef.current}
        loading={data.loading}
      />

      <ApprovalFilterBar
        filters={url.filters}
        onFiltersChange={url.handleFiltersChange}
        pendingCount={derived.pendingCount}
        totalCount={total}
        actionTypes={derived.actionTypes}
      />

      <RiskMetricCards
        riskCounts={derived.riskCounts}
        activeFilter={url.filters.riskLevel}
        onToggle={ctrl.handleRiskToggle}
      />

      {ctrl.emptyStateProps && <EmptyState {...ctrl.emptyStateProps} />}

      <ApprovalGroups ctrl={ctrl} />
      <ApprovalDrawerHost ctrl={ctrl} />
      <ApprovalBatchSection ctrl={ctrl} />
    </div>
  )
}
