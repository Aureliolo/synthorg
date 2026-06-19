import { useEffect, useState } from 'react'
import { Activity } from 'lucide-react'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { MetricCard } from '@/components/ui/metric-card'
import { SectionCard } from '@/components/ui/section-card'
import { SkeletonMetric } from '@/components/ui/skeleton'
import { getCallAnalytics } from '@/api/endpoints/budget'
import type { AnalyticsAggregation } from '@/api/types'
import { createLogger } from '@/lib/logger'
import { isAxiosError } from '@/utils/errors'
import { formatNumber } from '@/utils/format'

const log = createLogger('CallAnalyticsSection')

function pct(value: number): string {
  return `${Math.round(value * 100)}%`
}

function optionalPct(value: number | null): string {
  return value === null ? '--' : pct(value)
}

function optionalMs(value: number | null): string {
  return value === null ? '--' : `${Math.round(value)} ms`
}

function AnalyticsMetrics({ data }: { data: AnalyticsAggregation }) {
  const successRate = data.total_calls > 0 ? data.success_count / data.total_calls : 0
  return (
    <div className="grid grid-cols-4 gap-grid-gap max-[1023px]:grid-cols-2 max-[639px]:grid-cols-1">
      <MetricCard label="Total calls" value={formatNumber(data.total_calls)} />
      <MetricCard label="Success rate" value={pct(successRate)} />
      <MetricCard label="Retry rate" value={pct(data.retry_rate)} />
      <MetricCard label="Cache hit rate" value={optionalPct(data.cache_hit_rate)} />
      <MetricCard label="Avg latency" value={optionalMs(data.avg_latency_ms)} />
      <MetricCard label="P95 latency" value={optionalMs(data.p95_latency_ms)} />
      <MetricCard label="Orchestration ratio" value={pct(data.orchestration_ratio.ratio)} />
      <MetricCard label="Failures" value={formatNumber(data.failure_count)} />
    </div>
  )
}

function FinishReasonTable({ rows }: { rows: AnalyticsAggregation['by_finish_reason'] }) {
  if (rows.length === 0) return null
  return (
    <div className="mt-section-gap overflow-x-auto">
      <table className="w-full min-w-[20rem] text-sm">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wide text-muted-foreground">
            <th className="py-2 pr-4 font-medium">Finish reason</th>
            <th className="py-2 text-right font-medium">Calls</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([reason, count]) => (
            <tr key={reason} className="border-t border-border">
              <td className="py-2 pr-4 text-foreground">{reason}</td>
              <td className="py-2 text-right tabular-nums">{formatNumber(count)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/**
 * Per-call analytics aggregated over the recorded cost ledger: success /
 * retry / cache rates, latency percentiles, orchestration overhead, and a
 * finish-reason breakdown. Reads ``GET /budget/call-analytics``.
 */
export function CallAnalyticsSection() {
  const [data, setData] = useState<AnalyticsAggregation | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    getCallAnalytics(undefined, controller.signal)
      .then((result) => {
        setData(result)
        setLoading(false)
      })
      .catch((err: unknown) => {
        if (isAxiosError(err) && err.code === 'ERR_CANCELED') return
        log.warn('call analytics fetch failed', err)
        setError('Could not load call analytics.')
        setLoading(false)
      })
    return () => controller.abort()
  }, [])

  return (
    <SectionCard title="Call analytics" icon={Activity}>
      {loading ? (
        <div className="grid grid-cols-4 gap-grid-gap max-[1023px]:grid-cols-2 max-[639px]:grid-cols-1">
          {[1, 2, 3, 4].map((i) => (
            <SkeletonMetric key={i} />
          ))}
        </div>
      ) : error !== null ? (
        <ErrorBanner severity="warning" title="Call analytics unavailable" description={error} />
      ) : data === null || data.total_calls === 0 ? (
        <EmptyState
          icon={Activity}
          title="No call analytics yet"
          description="Per-call analytics appear once the org records its first LLM calls."
        />
      ) : (
        <>
          <AnalyticsMetrics data={data} />
          <FinishReasonTable rows={data.by_finish_reason} />
        </>
      )}
    </SectionCard>
  )
}
