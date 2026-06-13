/**
 * Cross-deployment analytics section for the meta-analytics page.
 *
 * Reads aggregated patterns and threshold recommendations from the
 * collector-role endpoints. The collector is opt-in, so a 503 (collector
 * disabled) degrades to a quiet "not enabled" note rather than a loud
 * error -- this surface is informational, not operational.
 */
import { useCallback, useEffect, useState } from 'react'
import { Globe } from 'lucide-react'
import { Collapsible } from '@/components/ui/collapsible'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { SectionCard } from '@/components/ui/section-card'
import { SkeletonText } from '@/components/ui/skeleton'
import { listPatterns, listRecommendations } from '@/api/endpoints/meta-analytics'
import type { AggregatedPattern, ThresholdRecommendation } from '@/api/types'
import { ErrorCode } from '@/api/types/errors'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { formatNumber } from '@/utils/format'
import { getErrorCode, getErrorMessage, isAxiosError } from '@/utils/errors'

const log = createLogger('CrossDeploymentSection')

interface CrossDeploymentState {
  patterns: readonly AggregatedPattern[]
  recommendations: readonly ThresholdRecommendation[]
  loading: boolean
  /** True when the collector role is disabled (503): show a quiet note. */
  unavailable: boolean
  error: string | null
}

/**
 * True only for a genuine 503 (collector disabled), which the backend
 * raises as `ServiceUnavailableError` (structured `SERVICE_UNAVAILABLE`
 * code, HTTP 503). Branching on the typed code rather than message text
 * keeps a real backend fault (e.g. a 500 whose prose happens to contain
 * "unavailable") from being masked as "not enabled". The HTTP-status
 * fallback covers envelopes that arrive without a structured code.
 */
function isServiceUnavailable(error: unknown): boolean {
  if (getErrorCode(error) === ErrorCode.SERVICE_UNAVAILABLE) return true
  return isAxiosError(error) && error.response?.status === 503
}

type FetchResults = readonly [
  PromiseSettledResult<readonly AggregatedPattern[]>,
  PromiseSettledResult<readonly ThresholdRecommendation[]>,
]

function fulfilledOr<T>(result: PromiseSettledResult<readonly T[]>): readonly T[] {
  return result.status === 'fulfilled' ? result.value : []
}

/** Reduce the settled pattern / recommendation results to display state. */
function deriveState(results: FetchResults): CrossDeploymentState {
  const rejections = results.filter(
    (r): r is PromiseRejectedResult => r.status === 'rejected',
  )
  if (rejections.length > 0) {
    // Prefer a real (non-503) failure for display: a genuine backend
    // error is more actionable than the "collector disabled" note, and
    // must never be hidden behind a sibling 503. Log every real failure.
    const real = rejections.find((r) => !isServiceUnavailable(r.reason))
    if (real) {
      log.error('cross-deployment fetch failed', {
        error: sanitizeForLog(getErrorMessage(real.reason)),
      })
      return {
        patterns: [],
        recommendations: [],
        loading: false,
        unavailable: false,
        error: getErrorMessage(real.reason),
      }
    }
    // All failures were 503: the collector is genuinely not enabled.
    return {
      patterns: [],
      recommendations: [],
      loading: false,
      unavailable: true,
      error: null,
    }
  }
  return {
    patterns: fulfilledOr(results[0]),
    recommendations: fulfilledOr(results[1]),
    loading: false,
    unavailable: false,
    error: null,
  }
}

function useCrossDeploymentData(): CrossDeploymentState {
  const [state, setState] = useState<CrossDeploymentState>({
    patterns: [],
    recommendations: [],
    loading: true,
    unavailable: false,
    error: null,
  })

  const load = useCallback(() => {
    void Promise.allSettled([listPatterns(), listRecommendations()]).then((results) => {
      setState(deriveState(results))
    })
  }, [])

  useEffect(() => {
    void Promise.resolve().then(load)
  }, [load])

  return state
}

function PatternsList({ patterns }: { patterns: readonly AggregatedPattern[] }) {
  return (
    <ul className="divide-y divide-border">
      {patterns.map((p) => (
        <li key={p.source_rule} className="flex items-center gap-4 py-2 text-sm">
          <span className="flex-1 font-medium text-foreground">{p.source_rule}</span>
          <span className="text-xs text-muted-foreground">
            {formatNumber(p.deployment_count)} deployments
          </span>
          <span className="text-xs text-muted-foreground">
            {(p.success_rate * 100).toFixed(0)}% success
          </span>
        </li>
      ))}
    </ul>
  )
}

function RecommendationsList({
  recommendations,
}: {
  recommendations: readonly ThresholdRecommendation[]
}) {
  return (
    <ul className="divide-y divide-border">
      {recommendations.map((r) => (
        <li key={`${r.rule_name}-${r.metric_name}`} className="space-y-1 py-2 text-sm">
          <div className="flex items-center gap-4">
            <span className="flex-1 font-medium text-foreground">{r.metric_name}</span>
            <span className="text-xs text-muted-foreground tabular-nums">
              {formatNumber(r.current_default)} &rarr; {formatNumber(r.recommended_value)}
            </span>
          </div>
          <p className="text-xs text-muted-foreground">{r.rationale}</p>
        </li>
      ))}
    </ul>
  )
}

function CrossDeploymentContent({ state }: { state: CrossDeploymentState }) {
  const { patterns, recommendations, loading, unavailable, error } = state
  if (loading) return <SkeletonText lines={4} />
  if (error != null) {
    return (
      <ErrorBanner
        severity="warning"
        title="Could not load cross-deployment analytics"
        description={error}
      />
    )
  }
  if (unavailable) {
    return (
      <EmptyState
        icon={Globe}
        title="Cross-deployment analytics not enabled"
        description="This deployment is not running the analytics collector role, so aggregated patterns are unavailable."
      />
    )
  }
  if (patterns.length === 0 && recommendations.length === 0) {
    return (
      <EmptyState
        icon={Globe}
        title="No aggregated patterns yet"
        description="Patterns and recommendations appear once enough deployments have reported outcome events."
      />
    )
  }
  return (
    <div className="space-y-section-gap">
      {patterns.length > 0 && <PatternsList patterns={patterns} />}
      {recommendations.length > 0 && <RecommendationsList recommendations={recommendations} />}
    </div>
  )
}

export function CrossDeploymentSection() {
  const state = useCrossDeploymentData()
  const count = state.patterns.length + state.recommendations.length

  return (
    <Collapsible title="Cross-deployment patterns" summary={count > 0 ? count : undefined}>
      <SectionCard title="Aggregated patterns &amp; recommendations" icon={Globe}>
        <CrossDeploymentContent state={state} />
      </SectionCard>
    </Collapsible>
  )
}
