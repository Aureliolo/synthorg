import { useMemo } from 'react'
import { MetricCard } from '@/components/ui/metric-card'
import { formatDateTime, formatNumber } from '@/utils/format'
import { formatLatency, formatErrorRate, formatTokenCount, formatCost } from '@/utils/providers'
import type { ProviderHealthSummary } from '@/api/types/providers'

interface ProviderHealthMetricsProps {
  health: ProviderHealthSummary
}

export function ProviderHealthMetrics({ health }: ProviderHealthMetricsProps) {
  const lastCheck = useMemo(
    () => formatDateTime(health.last_check_timestamp),
    [health.last_check_timestamp],
  )

  return (
    <div className="grid grid-cols-2 gap-grid-gap md:grid-cols-3 lg:grid-cols-6">
      <MetricCard
        label="Calls (24h)"
        value={formatNumber(health.calls_last_24h)}
      />
      <MetricCard
        label="Tokens (24h)"
        value={formatTokenCount(health.total_tokens_24h)}
      />
      <MetricCard
        label="Cost (24h)"
        value={formatCost(health.total_cost_24h)}
      />
      <MetricCard
        label="Avg Response Time"
        value={formatLatency(health.avg_response_time_ms ?? null)}
      />
      <MetricCard
        label="Error Rate (24h)"
        value={formatErrorRate(health.error_rate_percent_24h)}
      />
      <MetricCard
        label="Last Check"
        value={lastCheck}
      />
    </div>
  )
}
