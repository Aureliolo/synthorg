/**
 * SSRF-violation review queue.
 *
 * Lists outbound URLs the provider egress guard blocked, with a status
 * filter and cursor-paginated load-more. CEO / Manager operators allow or
 * deny each pending violation through a confirmation dialog.
 */
import { Check, ShieldAlert, ShieldCheck, ShieldX, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { SearchFilterSort } from '@/components/ui/search-filter-sort'
import { SectionCard } from '@/components/ui/section-card'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { Skeleton } from '@/components/ui/skeleton'
import { formatDateTime, formatRelativeTime } from '@/utils/format'
import type { SsrfViolationDTO } from '@/api/types'
import type { SsrfViolationStatus } from '@/api/types/enum-values.gen'
import {
  SSRF_STATUS_OPTIONS,
  useSsrfViolations,
  type SsrfViolationsController,
} from './useSsrfViolations'

const STATUS_ICON: Record<SsrfViolationStatus, typeof ShieldAlert> = {
  pending: ShieldAlert,
  allowed: ShieldCheck,
  denied: ShieldX,
}

const STATUS_TONE: Record<SsrfViolationStatus, string> = {
  pending: 'text-warning',
  allowed: 'text-success',
  denied: 'text-danger',
}

function StatusIndicator({ status }: { status: SsrfViolationStatus }) {
  const Icon = STATUS_ICON[status]
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs font-medium ${STATUS_TONE[status]}`}>
      <Icon className="size-3.5" aria-hidden="true" />
      {status.charAt(0).toUpperCase() + status.slice(1)}
    </span>
  )
}

function ViolationDetails({ violation }: { violation: SsrfViolationDTO }) {
  return (
    <dl className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-2">
      <div className="sm:col-span-2">
        <dt className="text-text-secondary">URL</dt>
        <dd className="break-all font-mono text-micro text-foreground">{violation.url}</dd>
      </div>
      <div>
        <dt className="text-text-secondary">Host</dt>
        <dd className="text-foreground">
          {violation.hostname}:{violation.port}
          {violation.resolved_ip !== null && ` (${violation.resolved_ip})`}
        </dd>
      </div>
      <div>
        <dt className="text-text-secondary">Provider</dt>
        <dd className="text-foreground">{violation.provider_name ?? 'Unknown'}</dd>
      </div>
      {violation.blocked_range !== null && (
        <div>
          <dt className="text-text-secondary">Blocked range</dt>
          <dd className="font-mono text-micro text-foreground">{violation.blocked_range}</dd>
        </div>
      )}
      <div>
        <dt className="text-text-secondary">Detected</dt>
        <dd className="text-foreground">
          <time dateTime={violation.timestamp} title={formatDateTime(violation.timestamp)}>
            {formatRelativeTime(violation.timestamp)}
          </time>
        </dd>
      </div>
    </dl>
  )
}

function ViolationActions({
  violation,
  ctrl,
}: {
  violation: SsrfViolationDTO
  ctrl: SsrfViolationsController
}) {
  if (!ctrl.canManage || violation.status !== 'pending') return null
  const busy = ctrl.resolvingId === violation.id
  return (
    <div className="mt-grid-gap flex justify-end gap-2">
      <Button
        variant="outline"
        size="sm"
        disabled={busy}
        onClick={() => ctrl.requestResolve(violation, 'allowed')}
      >
        <Check className="size-3.5" /> Allow
      </Button>
      <Button
        variant="destructive"
        size="sm"
        disabled={busy}
        onClick={() => ctrl.requestResolve(violation, 'denied')}
      >
        <X className="size-3.5" /> Deny
      </Button>
    </div>
  )
}

function ViolationCard({
  violation,
  ctrl,
}: {
  violation: SsrfViolationDTO
  ctrl: SsrfViolationsController
}) {
  return (
    <SectionCard title={violation.hostname} action={<StatusIndicator status={violation.status} />}>
      <ViolationDetails violation={violation} />
      <ViolationActions violation={violation} ctrl={ctrl} />
    </SectionCard>
  )
}

function ViolationsBody({ ctrl }: { ctrl: SsrfViolationsController }) {
  if (ctrl.loading && ctrl.violations.length === 0) {
    return (
      <div className="flex flex-col gap-grid-gap">
        {[1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-32 w-full" />
        ))}
      </div>
    )
  }
  if (!ctrl.error && ctrl.violations.length === 0) {
    return ctrl.emptyStateProps !== null ? <EmptyState {...ctrl.emptyStateProps} /> : null
  }
  return (
    <ul className="flex flex-col gap-grid-gap">
      {ctrl.violations.map((violation) => (
        <li key={violation.id}>
          <ViolationCard violation={violation} ctrl={ctrl} />
        </li>
      ))}
    </ul>
  )
}

function ResolveConfirmDialog({ ctrl }: { ctrl: SsrfViolationsController }) {
  const allow = ctrl.pending?.status === 'allowed'
  const verb = allow ? 'Allow' : 'Deny'
  const target =
    ctrl.pending !== null
      ? `${ctrl.pending.violation.hostname}:${ctrl.pending.violation.port}`
      : ''
  return (
    <ConfirmDialog
      open={ctrl.pending !== null}
      onOpenChange={(open) => {
        if (!open) ctrl.cancelResolve()
      }}
      title={`Confirm ${verb.toLowerCase()}`}
      description={target === '' ? '' : `${verb} outbound requests to ${target}?`}
      confirmLabel={verb}
      variant={allow ? 'default' : 'destructive'}
      loading={ctrl.resolvingId !== null}
      onConfirm={ctrl.confirmResolve}
    />
  )
}

export default function SsrfViolationsPage() {
  const ctrl = useSsrfViolations()

  return (
    <div className="space-y-section-gap">
      <ListHeader
        title="SSRF violations"
        description="Outbound URLs the provider egress guard blocked, pending review."
        count={ctrl.violations.length}
      />

      {ctrl.error !== null && (
        <ErrorBanner
          severity="error"
          title="Could not load SSRF violations"
          description={ctrl.error}
          onRetry={ctrl.retry}
        />
      )}

      <SearchFilterSort
        filters={
          <SegmentedControl
            label="Filter by status"
            value={ctrl.statusFilter}
            onChange={ctrl.handleStatusChange}
            options={SSRF_STATUS_OPTIONS}
            size="sm"
          />
        }
      />

      <ViolationsBody ctrl={ctrl} />

      {ctrl.hasMore && (
        <Button variant="secondary" onClick={ctrl.loadMore} disabled={ctrl.loadingMore}>
          {ctrl.loadingMore ? 'Loading...' : 'Load more'}
        </Button>
      )}

      <ResolveConfirmDialog ctrl={ctrl} />
    </div>
  )
}
