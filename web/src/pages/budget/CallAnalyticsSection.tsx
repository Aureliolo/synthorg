import { useEffect, useState } from 'react'
import { Activity } from 'lucide-react'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { MetricCard } from '@/components/ui/metric-card'
import { SectionCard } from '@/components/ui/section-card'
import { SkeletonMetric } from '@/components/ui/skeleton'
import { getCallAnalytics } from '@/api/endpoints/budget'
import type { AnalyticsAggregation } from '@/api/types/budget'
import { createLogger } from '@/lib/logger'
import { isAxiosError } from '@/utils/errors'
import { formatNumber, formatShare } from '@/utils/format'

const log = createLogger('CallAnalyticsSection')

function optionalMs(value: number | null): string {
  return value === null ? '--' : `${Math.round(value)} ms`
}

// The metric cards below, so the loading skeleton has the same shape as
// the loaded grid instead of one row popping into two.
const METRIC_COUNT = 8

function AnalyticsMetrics({ data }: { data: AnalyticsAggregation }) {
  // The rate is derived server-side over the calls that REPORTED an outcome.
  // Dividing successes by every call, as this did, reported the ones that
  // said nothing as failures: "40% success rate" beside "0 failures" over 293
  // calls, none of which had failed.
  const judged = data.success_count + data.failure_count
  return (
    <div className="grid grid-cols-4 gap-grid-gap max-[1023px]:grid-cols-2 max-[639px]:grid-cols-1">
      <MetricCard label="Total calls" value={formatNumber(data.total_calls)} />
      <MetricCard
        label="Success rate"
        value={formatShare(data.success_rate)}
        subText={
          judged === data.total_calls
            ? undefined
            : `of ${formatNumber(judged)} calls that reported one`
        }
      />
      <MetricCard label="Retry rate" value={formatShare(data.retry_rate)} />
      <MetricCard label="Cached input share" value={formatShare(data.cached_input_share)} />
      <MetricCard label="Avg latency" value={optionalMs(data.avg_latency_ms)} />
      <MetricCard label="P95 latency" value={optionalMs(data.p95_latency_ms)} />
      <MetricCard label="Orchestration ratio" value={formatShare(data.orchestration_ratio.ratio)} />
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
 * retry rates, the cached share of input tokens, latency percentiles,
 * orchestration overhead, and a
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
        if (controller.signal.aborted) return
        setData(result)
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return
        if (isAxiosError(err) && err.code === 'ERR_CANCELED') return
        log.warn('call analytics fetch failed', err)
        setError('Could not load call analytics.')
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [])

  return (
    <SectionCard title="Call analytics" icon={Activity}>
      {loading ? (
        <div className="grid grid-cols-4 gap-grid-gap max-[1023px]:grid-cols-2 max-[639px]:grid-cols-1">
          {Array.from({ length: METRIC_COUNT }, (_, i) => (
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
