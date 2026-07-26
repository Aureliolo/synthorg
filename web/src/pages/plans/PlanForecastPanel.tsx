import { CircleAlert, Wallet } from 'lucide-react'

import type { Forecast, ForecastDecision } from '@/api/types/budget'
import { SectionCard } from '@/components/ui/section-card'
import { Skeleton } from '@/components/ui/skeleton'
import { StatusPill } from '@/components/ui/status-pill'
import type { StatusPillTone } from '@/components/ui/status-pill'
import { usePlanForecast } from '@/hooks/usePlanForecast'
import { formatCurrency } from '@/utils/format'

const DECISION_LABEL: Record<ForecastDecision, string> = {
  pending: 'Awaiting decision',
  approved: 'Approved',
  rejected: 'Rejected',
  superseded: 'Superseded',
}

const DECISION_TONE: Record<ForecastDecision, StatusPillTone> = {
  pending: 'warning',
  approved: 'success',
  rejected: 'danger',
  superseded: 'text-secondary',
}

function DecisionPill({ decision }: { decision: ForecastDecision }) {
  return <StatusPill tone={DECISION_TONE[decision]}>{DECISION_LABEL[decision]}</StatusPill>
}

function ForecastBody({ forecast }: { forecast: Forecast }) {
  const { currency } = forecast
  return (
    <div className="space-y-3">
      <div className="flex items-baseline gap-2">
        <span className="text-2xl font-semibold text-foreground">
          {formatCurrency(forecast.estimated_cost, currency)}
        </span>
        <span className="text-xs text-muted-foreground">estimated</span>
      </div>
      <p className="text-xs text-text-secondary">
        Range {formatCurrency(forecast.lower_bound, currency)} to{' '}
        {formatCurrency(forecast.upper_bound, currency)}
        {forecast.ceiling_amount !== null && (
          <>
            {' · ceiling '}
            {formatCurrency(forecast.ceiling_amount, currency)}
          </>
        )}
      </p>
      {forecast.halt_context !== null && (
        <p className="flex items-center gap-1.5 text-xs text-danger">
          <CircleAlert className="size-3.5 shrink-0" aria-hidden="true" />
          Run halted: the hard ceiling of{' '}
          {formatCurrency(forecast.halt_context.ceiling_amount, currency)} was crossed.
        </p>
      )}
    </div>
  )
}

/**
 * The cost forecast the plan was released alongside: the estimate with its
 * uncertainty band, the operator's decision state, and any hard-ceiling halt,
 * so budget sits next to the plan the human is about to approve. Hidden when
 * the plan carries no forecast; a fetch error surfaces inline rather than
 * blanking the workspace.
 */
export function PlanForecastPanel({ forecastId }: { forecastId: string | null }) {
  const { forecast, loading, error } = usePlanForecast(forecastId)
  if (forecastId === null) return null
  return (
    <SectionCard
      title="Cost forecast"
      icon={Wallet}
      action={forecast !== null ? <DecisionPill decision={forecast.decision} /> : undefined}
    >
      {loading && forecast === null ? (
        <Skeleton className="h-16 w-full" />
      ) : error !== null && forecast === null ? (
        <p className="text-xs text-muted-foreground">
          Cost forecast unavailable: {error}
        </p>
      ) : forecast !== null ? (
        <ForecastBody forecast={forecast} />
      ) : null}
    </SectionCard>
  )
}
