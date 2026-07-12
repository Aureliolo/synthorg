import { MetricCard } from '@/components/ui/metric-card'
import type { PlanStats } from '@/utils/plans'

function pluralItems(count: number): string {
  return `${count} item${count === 1 ? '' : 's'}`
}

/** At-a-glance review scorecard: size, risk, and shape of the proposed plan. */
export function PlanMetricsHeader({ stats }: { stats: PlanStats }) {
  return (
    <div className="grid grid-cols-2 gap-grid-gap lg:grid-cols-4">
      <MetricCard
        label="Plan items"
        value={stats.totalItems}
        subText={`${stats.highComplexity} high-effort`}
      />
      <MetricCard
        label="Needs your review"
        value={stats.flaggedItems}
        subText={`${stats.highStakes} high-stakes`}
      />
      <MetricCard
        label="Critical path"
        value={stats.criticalPathLength}
        subText={`of ${pluralItems(stats.totalItems)}`}
        progress={{ current: stats.criticalPathLength, total: stats.totalItems }}
      />
      <MetricCard
        label="Dependencies"
        value={stats.dependencyEdges}
        subText={stats.unowned > 0 ? `${stats.unowned} unassigned` : 'all assigned'}
      />
    </div>
  )
}
