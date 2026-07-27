import { MetricCard } from '@/components/ui/metric-card'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import type {
  ScalingDecisionResponse,
  ScalingSignalResponse,
  ScalingStrategyResponse,
} from '@/api/types/scaling'

interface ScalingMetricsProps {
  strategies: readonly ScalingStrategyResponse[]
  decisions: readonly ScalingDecisionResponse[]
  signals: readonly ScalingSignalResponse[]
}

function findSignal(
  signals: readonly ScalingSignalResponse[],
  name: string,
): number | null {
  const signal = signals.find((s) => s.name === name)
  return signal?.value ?? null
}

export function ScalingMetrics({
  strategies,
  decisions,
  signals,
}: ScalingMetricsProps) {
  const activeStrategies = strategies.filter((s) => s.enabled).length
  const pendingDecisions = decisions.length
  const utilization = findSignal(signals, 'avg_utilization')
  const burnRate = findSignal(signals, 'burn_rate_percent')

  return (
    <StaggerGroup className="grid grid-cols-1 gap-grid-gap sm:grid-cols-2 xl:grid-cols-4">
      <StaggerItem className="h-full">
        <MetricCard
          className="h-full"
          label="Active Strategies"
          value={activeStrategies}
          subText={`of ${strategies.length} total`}
        />
      </StaggerItem>
      <StaggerItem className="h-full">
        <MetricCard
          className="h-full"
          label="Pending Decisions"
          value={pendingDecisions}
        />
      </StaggerItem>
      <StaggerItem className="h-full">
        <MetricCard
          className="h-full"
          label="Avg Utilization"
          value={utilization !== null ? `${Math.round(utilization * 100)}%` : 'N/A'}
        />
      </StaggerItem>
      <StaggerItem className="h-full">
        <MetricCard
          className="h-full"
          label="Budget Burn"
          value={burnRate !== null ? `${Math.round(burnRate)}%` : 'N/A'}
        />
      </StaggerItem>
    </StaggerGroup>
  )
}
