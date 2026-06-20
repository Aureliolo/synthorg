import { Dna } from 'lucide-react'

import { cn } from '@/lib/utils'
import { EmptyState } from '@/components/ui/empty-state'
import { MetricCard } from '@/components/ui/metric-card'
import { formatDateTime } from '@/utils/format'

import type {
  EvolutionAxisStat,
  EvolutionRecentOutcome,
  EvolutionSummary,
} from '@/api/endpoints/meta'

interface MetaEvolutionViewProps {
  summary: EvolutionSummary | null
  axes: readonly EvolutionAxisStat[]
}

export function MetaEvolutionView({ summary, axes }: MetaEvolutionViewProps) {
  const total = summary?.total_proposals ?? 0
  if (total === 0 && axes.length === 0) {
    return (
      <EmptyState
        icon={Dna}
        title="No evolution outcomes yet"
        description="Per-agent adaptation outcomes appear here once the evolution loop runs and records applied or rejected proposals."
      />
    )
  }

  return (
    <div className="space-y-section-gap">
      <EvolutionMetrics summary={summary} />
      {axes.length > 0 && <AxisStats axes={axes} />}
      {summary && summary.recent_outcomes.length > 0 && (
        <RecentOutcomes outcomes={summary.recent_outcomes} />
      )}
    </div>
  )
}

interface EvolutionMetricsProps {
  summary: EvolutionSummary | null
}

function EvolutionMetrics({ summary }: EvolutionMetricsProps) {
  const approval = summary ? `${(summary.approval_rate * 100).toFixed(0)}%` : '0%'
  return (
    <div className="grid grid-cols-1 gap-grid-gap md:grid-cols-3">
      <MetricCard label="Total Proposals" value={summary?.total_proposals ?? 0} />
      <MetricCard label="Approval Rate" value={approval} />
      <MetricCard
        label="Most Adapted Axis"
        value={summary?.most_adapted_axis ?? '--'}
      />
    </div>
  )
}

interface AxisStatsProps {
  axes: readonly EvolutionAxisStat[]
}

function AxisStats({ axes }: AxisStatsProps) {
  const max = Math.max(...axes.map((a) => a.count), 1)
  return (
    <div>
      <p className="mb-2 text-xs font-medium text-muted-foreground">
        Outcomes by axis
      </p>
      <div className="space-y-2">
        {axes.map((axis) => (
          <AxisBar key={axis.axis} axis={axis} max={max} />
        ))}
      </div>
    </div>
  )
}

interface AxisBarProps {
  axis: EvolutionAxisStat
  max: number
}

function AxisBar({ axis, max }: AxisBarProps) {
  const widthPct = (axis.count / max) * 100
  return (
    <div className="flex items-center gap-3">
      <span className="w-40 shrink-0 truncate text-xs text-foreground">
        {axis.axis}
      </span>
      <div className="h-2 flex-1 rounded-full bg-muted">
        <div
          className="h-full rounded-full bg-accent"
          style={{ width: `${widthPct}%` }}
        />
      </div>
      <span className="w-8 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
        {axis.count}
      </span>
    </div>
  )
}

interface RecentOutcomesProps {
  outcomes: readonly EvolutionRecentOutcome[]
}

function RecentOutcomes({ outcomes }: RecentOutcomesProps) {
  return (
    <div>
      <p className="mb-2 text-xs font-medium text-muted-foreground">
        Recent outcomes
      </p>
      <ul className="space-y-1">
        {outcomes.map((outcome) => (
          <OutcomeRow
            key={`${outcome.agent_id}-${outcome.axis}-${outcome.proposed_at}`}
            outcome={outcome}
          />
        ))}
      </ul>
    </div>
  )
}

interface OutcomeRowProps {
  outcome: EvolutionRecentOutcome
}

function OutcomeRow({ outcome }: OutcomeRowProps) {
  return (
    <li className="flex items-center justify-between gap-2 rounded-md border border-border bg-card px-3 py-1.5 text-xs">
      <span className="truncate text-foreground">{outcome.agent_id}</span>
      <span className="shrink-0 text-muted-foreground">{outcome.axis}</span>
      <span
        className={cn(
          'shrink-0 rounded-full px-2 py-0.5 font-medium',
          outcome.applied
            ? 'bg-success/15 text-success'
            : 'bg-muted text-muted-foreground',
        )}
      >
        {outcome.applied ? 'Applied' : 'Rejected'}
      </span>
      <span className="shrink-0 text-muted-foreground">
        {formatDateTime(outcome.proposed_at)}
      </span>
    </li>
  )
}
