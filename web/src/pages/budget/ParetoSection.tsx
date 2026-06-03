import { ArrowDownToLine, Sparkles } from 'lucide-react'

import { SectionCard } from '@/components/ui/section-card'
import { EmptyState } from '@/components/ui/empty-state'
import { cn } from '@/lib/utils'
import type { ParetoFrontier } from '@/api/types'

export interface ParetoSectionProps {
  frontier: ParetoFrontier | null
  loading?: boolean
}

export function ParetoSection({ frontier, loading = false }: ParetoSectionProps) {
  return (
    <SectionCard
      title="Cost / Quality Pareto"
      icon={Sparkles}
      action={frontier ? <SourceBadge source={frontier.source} /> : undefined}
    >
      {loading ? (
        <ParetoSkeleton />
      ) : !frontier || frontier.points.length === 0 ? (
        <EmptyState
          title="No downgrade opportunities"
          description="Run more tasks for the analyzer to identify lower-cost models that preserve quality."
        />
      ) : (
        <ul className="flex flex-col gap-section-gap">
          {frontier.points.map((point) => (
            <ParetoRow key={point.role_id} point={point} />
          ))}
        </ul>
      )}
    </SectionCard>
  )
}

interface ParetoRowProps {
  point: ParetoFrontier['points'][number]
}

function ParetoRow({ point }: ParetoRowProps) {
  return (
    <li className="flex items-center justify-between gap-grid-gap rounded-lg border border-border bg-card p-card">
      <div className="flex flex-col gap-1">
        <div className="text-sm font-medium text-foreground">
          {point.role_label}
        </div>
        <div className="text-xs text-muted-foreground">
          {point.current_model}
          <ArrowDownToLine
            aria-hidden="true"
            className="mx-1 inline size-3 align-middle text-muted-foreground"
          />
          {point.candidate_model}
        </div>
      </div>
      <div className="flex items-center gap-3 text-right">
        <Metric
          label="Save"
          value={`${point.cost_saving_pct.toFixed(0)}%`}
          tone="positive"
        />
        <Metric
          label="Quality"
          value={`-${point.quality_delta_pct.toFixed(0)}%`}
          tone={point.quality_delta_pct > 25 ? 'warning' : 'neutral'}
        />
      </div>
    </li>
  )
}

interface MetricProps {
  label: string
  value: string
  tone: 'positive' | 'neutral' | 'warning'
}

function Metric({ label, value, tone }: MetricProps) {
  return (
    <div className="flex flex-col items-end">
      <span className="text-xs text-text-muted uppercase tracking-wide">
        {label}
      </span>
      <span
        className={cn(
          'font-mono text-sm font-semibold',
          tone === 'positive' && 'text-success',
          tone === 'neutral' && 'text-foreground',
          tone === 'warning' && 'text-warning',
        )}
      >
        {value}
      </span>
    </div>
  )
}

interface SourceBadgeProps {
  source: string
}

type BadgeKind = 'measured' | 'estimated' | 'mixed'

const BADGE_CLASS = {
  measured: 'border border-success/30 bg-success/10 text-success',
  estimated: 'border border-warning/30 bg-warning/10 text-warning',
  mixed: 'border border-warning/30 bg-warning/10 text-warning',
} as const satisfies Record<BadgeKind, string>

const BADGE_LABEL = {
  measured: 'measured',
  estimated: 'estimated',
  mixed: 'mixed',
} as const satisfies Record<BadgeKind, string>

const BADGE_TITLE = {
  measured: 'Measured per-model benchmark scores',
  estimated: 'Stub benchmark data; awaiting measured scores',
  mixed: 'Mixed provenance: some roles measured, some stub',
} as const satisfies Record<BadgeKind, string>

// The frontier source joins each point's provenance, and a point blends
// its current/candidate scores, so a single string can carry both
// 'benchmark:' (measured) and 'stub:' tokens. A measured-current /
// stub-candidate mix must not read as fully measured.
function badgeKind(source: string): BadgeKind {
  const hasStub = source.includes('stub:')
  const hasMeasured = source.includes('benchmark:')
  if (hasStub && hasMeasured) return 'mixed'
  return hasStub ? 'estimated' : 'measured'
}

function SourceBadge({ source }: SourceBadgeProps) {
  const kind = badgeKind(source)
  return (
    <span
      role="img"
      aria-label={BADGE_TITLE[kind]}
      className={cn(
        'inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-medium',
        BADGE_CLASS[kind],
      )}
      title={BADGE_TITLE[kind]}
    >
      {BADGE_LABEL[kind]}
    </span>
  )
}

function ParetoSkeleton() {
  return (
    <div className="flex flex-col gap-section-gap">
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="h-16 animate-pulse rounded-lg border border-border bg-card"
        />
      ))}
    </div>
  )
}
