import type { TaskStructure } from '@/api/types/enums'
import { MetricCard } from '@/components/ui/metric-card'
import type { PlanStats } from '@/utils/plans'

function pluralItems(count: number): string {
  return `${count} item${count === 1 ? '' : 's'}`
}

const STRUCTURE_LABEL: Record<TaskStructure, string> = {
  // A durable plan always names a resolved structure, so 'auto' reaching the
  // review header means decomposition produced one that skipped the service.
  auto: 'Unresolved',
  sequential: 'Sequential',
  parallel: 'Parallel',
  mixed: 'Mixed',
}

/** At-a-glance review scorecard: size, risk, and shape of the proposed plan. */
export function PlanMetricsHeader({
  stats,
  taskStructure,
}: {
  stats: PlanStats
  taskStructure: TaskStructure
}) {
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
      {stats.criticalPathLength > 0 ? (
        <MetricCard
          label="Critical path"
          value={stats.criticalPathLength}
          subText={`of ${pluralItems(stats.totalItems)}`}
          progress={{ current: stats.criticalPathLength, total: stats.totalItems }}
        />
      ) : (
        // On a sequential (or unbranched) plan the critical path is every item,
        // which is no signal, so show the execution shape instead.
        <MetricCard
          label="Execution shape"
          value={STRUCTURE_LABEL[taskStructure]}
          subText="no critical path"
        />
      )}
      <MetricCard
        label="Dependencies"
        value={stats.dependencyEdges}
        subText={stats.unowned > 0 ? `${stats.unowned} unassigned` : 'all assigned'}
      />
    </div>
  )
}
