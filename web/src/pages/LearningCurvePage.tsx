/**
 * Learning curve analytics.
 *
 * Charts the golden-company benchmark score across recorded runs so
 * the org's improvement (or regression) is visible at a glance. Reads
 * the curve assembled from the configured scorecard history directory;
 * an empty curve renders an explanatory empty state rather than an
 * error.
 */
import { useEffect, useState } from 'react'
import { Loader2, TrendingUp } from 'lucide-react'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { ListHeader } from '@/components/ui/list-header'
import { MetricCard } from '@/components/ui/metric-card'
import { getLearningCurve } from '@/api/endpoints/learning'
import type { LearningCurve } from '@/api/types'
import { BenchmarkScoreChart } from '@/pages/learning/BenchmarkScoreChart'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { formatNumber } from '@/utils/format'
import { getErrorMessage } from '@/utils/errors'

const log = createLogger('LearningCurvePage')

function LearningSummaryCards({ curve }: { curve: LearningCurve }) {
  const points = curve.points
  const latest = points[points.length - 1]
  const best = points.reduce((max, p) => Math.max(max, p.total), 0)
  const regressionCount = points.filter((p) => p.is_regression).length
  const change =
    latest && points.length > 1
      ? { value: Math.abs(latest.delta), direction: latest.delta >= 0 ? ('up' as const) : ('down' as const) }
      : undefined

  return (
    <div className="grid grid-cols-1 gap-grid-gap md:grid-cols-3">
      <MetricCard
        label="Latest score"
        value={latest ? formatNumber(latest.total) : '--'}
        change={change}
        subText={latest ? `${Math.round(latest.score_fraction * 100)}% of max` : undefined}
      />
      <MetricCard label="Best score" value={points.length > 0 ? formatNumber(best) : '--'} />
      <MetricCard
        label="Runs recorded"
        value={formatNumber(points.length)}
        subText={regressionCount > 0 ? `${regressionCount} regression(s)` : 'No regressions'}
      />
    </div>
  )
}

export default function LearningCurvePage() {
  const [curve, setCurve] = useState<LearningCurve | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    void Promise.resolve().then(async () => {
      if (cancelled) return
      setLoading(true)
      setError(null)
      try {
        const result = await getLearningCurve()
        // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition -- flipped by effect cleanup during the await; CFA cannot see the closure mutation
        if (cancelled) return
        setCurve(result)
      } catch (err) {
        // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition -- flipped by effect cleanup during the await; CFA cannot see the closure mutation
        if (cancelled) return
        const message = getErrorMessage(err)
        log.error('getLearningCurve failed', { error: sanitizeForLog(message) })
        setError(message)
      } finally {
        // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition -- flipped by effect cleanup during the await; CFA cannot see the closure mutation
        if (!cancelled) setLoading(false)
      }
    })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <div className="space-y-section-gap">
      <ListHeader title="Learning curve" />

      {error && (
        <ErrorBanner severity="error" title="Could not load learning curve" description={error} />
      )}

      {curve?.has_regression && (
        <ErrorBanner
          severity="warning"
          title="Benchmark regression detected"
          description="At least one recorded run scored materially below its predecessor."
        />
      )}

      {loading && !curve ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="size-6 animate-spin text-text-muted" />
        </div>
      ) : curve ? (
        <>
          <LearningSummaryCards curve={curve} />
          <ErrorBoundary level="section">
            <BenchmarkScoreChart curve={curve} />
          </ErrorBoundary>
        </>
      ) : (
        <div className="flex items-center justify-center py-12 text-text-muted">
          <TrendingUp className="mr-2 size-5" />
          No learning data available
        </div>
      )}
    </div>
  )
}
