/**
 * Per-model serviceability, scoped to one provider or fleet-wide.
 *
 * Deliberately separate from the health metrics beside it: those describe a
 * connection over 24 hours and count a reachability probe as evidence, so a
 * model that started queueing an hour ago still reads healthy there. This is
 * the half only real calls can measure.
 */
import { Activity } from 'lucide-react'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ProviderHealthBadge } from '@/components/ui/provider-health-badge'
import { SectionCard } from '@/components/ui/section-card'
import { SkeletonText } from '@/components/ui/skeleton'
import {
  PROVIDER_OUTCOME_CLASS_VALUES,
  type ModelServiceability,
  type ProviderOutcomeClass,
} from '@/api/types/providers'
import { formatDateTime, formatNumber } from '@/utils/format'
import { formatLatency } from '@/utils/providers'
import {
  serviceabilityRowKey,
  useServiceability,
  type ServiceabilityController,
} from './useServiceability'

/**
 * Operator-facing wording per outcome class. A ``Record`` over the generated
 * union rather than a hand-listed array, so an outcome class added backend-side
 * fails the type-check here instead of quietly dropping out of the summary.
 */
const OUTCOME_LABELS: Record<ProviderOutcomeClass, string> = {
  success: 'succeeded',
  rate_limit: 'throttled',
  quota_exceeded: 'quota exhausted',
  payment_required: 'balance empty',
  timeout: 'timed out',
  connection: 'unreachable',
  internal: 'server error',
  overloaded: 'overloaded',
  invalid_request: 'invalid request',
  auth: 'auth rejected',
  content_filter: 'content filtered',
  not_found: 'model not found',
  other: 'other',
}

/** Shown where a record carries no model, which only a probe can produce. */
const NO_MODEL_LABEL = 'connection only'

function failureSummary(row: ModelServiceability): string {
  const parts = PROVIDER_OUTCOME_CLASS_VALUES.flatMap((outcome) => {
    if (outcome === 'success') return []
    const count = row.outcome_counts[outcome] ?? 0
    return count > 0 ? [`${formatNumber(count)} ${OUTCOME_LABELS[outcome]}`] : []
  })
  return parts.length > 0 ? parts.join(', ') : 'none'
}

function ServiceabilityRow({
  row,
  showProvider,
}: {
  row: ModelServiceability
  showProvider: boolean
}) {
  return (
    <tr className="border-b border-border last:border-0">
      <th scope="row" className="py-2 pr-4 text-left align-top font-normal">
        <div className="text-sm font-medium text-foreground">
          {row.model ?? NO_MODEL_LABEL}
        </div>
        {showProvider && (
          <div className="text-xs text-muted-foreground">{row.provider_name}</div>
        )}
      </th>
      <td className="py-2 pr-4 align-top">
        <ProviderHealthBadge status={row.verdict} label />
      </td>
      <td className="py-2 pr-4 align-top tabular-nums">
        {formatNumber(row.call_count)}
      </td>
      <td className="py-2 pr-4 align-top text-xs text-muted-foreground">
        {failureSummary(row)}
      </td>
      <td className="py-2 pr-4 align-top tabular-nums">
        {formatLatency(row.latency?.p50_ms ?? null)}
      </td>
      {/* p99 rather than a mean: one fast reply and one five-minute reply
          average to a latency neither call took, which is how the incident
          that motivated this table stayed invisible. */}
      <td className="py-2 pr-4 align-top tabular-nums">
        {formatLatency(row.latency?.p99_ms ?? null)}
      </td>
      <td className="py-2 align-top text-xs text-muted-foreground">
        {formatDateTime(row.last_call_timestamp)}
      </td>
    </tr>
  )
}

function ServiceabilityTable({
  rows,
  showProvider,
}: {
  rows: readonly ModelServiceability[]
  showProvider: boolean
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left">
        <thead>
          <tr className="border-b border-border text-xs font-medium text-muted-foreground">
            <th scope="col" className="py-2 pr-4 font-medium">Model</th>
            <th scope="col" className="py-2 pr-4 font-medium">Verdict</th>
            <th scope="col" className="py-2 pr-4 font-medium">Calls</th>
            <th scope="col" className="py-2 pr-4 font-medium">Failures</th>
            <th scope="col" className="py-2 pr-4 font-medium">p50</th>
            <th scope="col" className="py-2 pr-4 font-medium">p99</th>
            <th scope="col" className="py-2 font-medium">Last call</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <ServiceabilityRow
              key={serviceabilityRowKey(row)}
              row={row}
              showProvider={showProvider}
            />
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ServiceabilityBody({
  ctrl,
  showProvider,
}: {
  ctrl: ServiceabilityController
  showProvider: boolean
}) {
  const { state } = ctrl
  if (state.loading) return <SkeletonText lines={4} />
  if (state.error != null) {
    return (
      <ErrorBanner
        severity="warning"
        title="Could not load serviceability"
        description={state.error}
        onRetry={ctrl.load}
      />
    )
  }
  if (state.rows.length === 0) {
    return (
      <EmptyState
        icon={Activity}
        title="No calls in the window"
        description="Verdicts come from calls this installation actually made, so a model nothing has used yet has none."
      />
    )
  }
  return <ServiceabilityTable rows={state.rows} showProvider={showProvider} />
}

export interface ProviderServiceabilitySectionProps {
  /** Scope to one provider, or show every served pair when omitted. */
  providerName?: string | undefined
}

export function ProviderServiceabilitySection({
  providerName,
}: ProviderServiceabilitySectionProps) {
  const ctrl = useServiceability(providerName)
  return (
    <SectionCard title="Serviceability" icon={Activity}>
      <ServiceabilityBody ctrl={ctrl} showProvider={providerName === undefined} />
    </SectionCard>
  )
}
