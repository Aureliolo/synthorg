import { Check, DollarSign, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { formatCurrency } from '@/utils/format'
import type { Forecast } from '@/api/types'

export interface CostForecastApprovalCardProps {
  forecast: Forecast
  mutating?: boolean
  onApprove: (ceilingAmount: number | null) => void
  onReject: () => void
  onOpenDetail?: (forecastId: string) => void
  className?: string
}

/**
 * Approvals-queue surface for a pending cost forecast. Shows the
 * estimated cost band and exposes Approve / Reject inline. The
 * full ceiling editor lives in BudgetForecastDialog; clicking the
 * title opens the dialog via onOpenDetail.
 */
function ForecastHeader({
  forecast,
  onOpenDetail,
}: {
  forecast: Forecast
  onOpenDetail: ((forecastId: string) => void) | undefined
}) {
  return (
    <div className="flex items-start gap-3">
      <span
        className="mt-1.5 inline-flex size-7 shrink-0 items-center justify-center rounded-full bg-accent/10 text-accent"
        aria-hidden="true"
      >
        <DollarSign className="size-4" />
      </span>
      <div className="min-w-0 flex-1">
        <button
          type="button"
          className="text-left text-sm font-medium text-foreground hover:text-accent transition-colors truncate block w-full"
          onClick={() => onOpenDetail?.(forecast.forecast_id)}
          disabled={onOpenDetail === undefined}
        >
          Pre-flight cost forecast
        </button>
        <div className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-text-secondary">
          <span className="font-mono">
            {formatCurrency(forecast.estimated_cost, forecast.currency)}
          </span>
          <span aria-hidden="true">--</span>
          <span className="font-mono">
            range {formatCurrency(forecast.lower_bound, forecast.currency)}
            {' – '}
            {formatCurrency(forecast.upper_bound, forecast.currency)}
          </span>
        </div>
      </div>
      <DecisionBadge decision={forecast.decision} />
    </div>
  )
}

function DecisionBadge({ decision }: { decision: Forecast['decision'] }) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-medium shrink-0',
        decision === 'pending' && 'border-warning/30 bg-warning/10 text-warning',
        decision === 'approved' && 'border-success/30 bg-success/10 text-success',
        decision === 'rejected' && 'border-danger/30 bg-danger/10 text-danger',
        decision === 'superseded' && 'border-muted/30 bg-muted/10 text-muted-foreground',
      )}
    >
      {decision}
    </span>
  )
}

function ForecastActions({
  mutating,
  onApprove,
  onReject,
}: {
  mutating: boolean
  onApprove: () => void
  onReject: () => void
}) {
  return (
    <div className="mt-3 flex items-center gap-2">
      <Button
        size="sm"
        variant="outline"
        className="border-success/30 text-success hover:bg-success/10"
        onClick={onApprove}
        disabled={mutating}
      >
        <Check className="size-3.5" />
        Approve
      </Button>
      <Button
        size="sm"
        variant="outline"
        className="border-danger/30 text-danger hover:bg-danger/10"
        onClick={onReject}
        disabled={mutating}
      >
        <X className="size-3.5" />
        Reject
      </Button>
    </div>
  )
}

export function CostForecastApprovalCard({
  forecast,
  mutating = false,
  onApprove,
  onReject,
  onOpenDetail,
  className,
}: CostForecastApprovalCardProps) {
  const isPending = forecast.decision === 'pending'
  return (
    <div
      role="article"
      aria-label={`Cost forecast ${forecast.forecast_id.slice(0, 8)}`}
      className={cn(
        'rounded-lg border bg-card p-card transition-all duration-[var(--so-transition-default)]',
        isPending
          ? 'border-border hover:bg-card-hover hover:-translate-y-px hover:shadow-[var(--so-shadow-card-hover)]'
          : 'border-border opacity-70',
        className,
      )}
    >
      <ForecastHeader forecast={forecast} onOpenDetail={onOpenDetail} />
      {isPending && (
        <ForecastActions
          mutating={mutating}
          onApprove={() => onApprove(null)}
          onReject={onReject}
        />
      )}
    </div>
  )
}
