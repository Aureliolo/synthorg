import { Scale } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { ListHeader } from '@/components/ui/list-header'
import { ROUTES } from '@/router/routes'
import { useScalingData } from '@/hooks/useScalingData'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getErrorMessage } from '@/utils/errors'

const log = createLogger('ScalingPage')

import { DecisionHistory } from './scaling/DecisionHistory'
import { PromotionCycleSection } from './scaling/PromotionCycleSection'
import { ScalingMetrics } from './scaling/ScalingMetrics'
import { ScalingSkeleton } from './scaling/ScalingSkeleton'
import { SignalGauges } from './scaling/SignalGauges'
import { StrategyControls } from './scaling/StrategyControls'

type EvaluateNow = ReturnType<typeof useScalingData>['evaluateNow']

function evaluationResultToast(count: number): Parameters<ReturnType<typeof useToastStore.getState>['add']>[0] {
  if (count > 0) {
    return { variant: 'success', title: `Evaluation produced ${count} decision(s)` }
  }
  return { variant: 'info', title: 'Evaluation produced no decisions' }
}

function useEvaluateNow(evaluateNow: EvaluateNow): () => Promise<void> {
  const addToast = useToastStore((s) => s.add)
  return async () => {
    try {
      const results = await evaluateNow()
      addToast(evaluationResultToast(results.length))
    } catch (err) {
      log.error('Evaluation failed', err)
      addToast({
        variant: 'error',
        title: 'Could not evaluate scaling',
        description: getErrorMessage(err),
      })
    }
  }
}

export default function ScalingPage() {
  const {
    strategies,
    decisions,
    signals,
    loading,
    error,
    evaluating,
    isRefetching,
    wsConnected,
    evaluateNow,
  } = useScalingData()

  const handleEvaluateNow = useEvaluateNow(evaluateNow)

  if (loading && strategies.length === 0) {
    return <ScalingSkeleton />
  }

  return (
    <div className="flex flex-col gap-section-gap">
      <ListHeader
        title="Dynamic Scaling"
        refreshing={isRefetching}
        primaryAction={
          <Button size="sm" onClick={handleEvaluateNow} disabled={evaluating}>
            {evaluating ? 'Evaluating...' : 'Evaluate Now'}
          </Button>
        }
      />

      {error && (
        <ErrorBanner
          severity="error"
          title="Could not load scaling data"
          description={error}
        />
      )}

      {!wsConnected && (
        <ErrorBanner
          variant="offline"
          title="Real-time updates unavailable"
          description="Scaling decisions may be stale until the connection recovers."
        />
      )}

      <ScalingSections
        strategies={strategies}
        decisions={decisions}
        signals={signals}
        error={error}
      />

      {/* Cluster-wide promotion cycle */}
      <ErrorBoundary level="section">
        <PromotionCycleSection />
      </ErrorBoundary>
    </div>
  )
}

interface ScalingSectionsProps {
  strategies: ReturnType<typeof useScalingData>['strategies']
  decisions: ReturnType<typeof useScalingData>['decisions']
  signals: ReturnType<typeof useScalingData>['signals']
  error: string | null
}

function ScalingSections({ strategies, decisions, signals, error }: ScalingSectionsProps) {
  if (!error && strategies.length === 0) {
    return (
      <EmptyState
        icon={Scale}
        title="No scaling strategies configured"
        description="Configure a scaling strategy to let the org grow or shrink its agent roster automatically."
        learnMore={{
          href: ROUTES.SETTINGS_NAMESPACE.replace(':namespace', 'coordination'),
          label: 'Open coordination settings',
        }}
      />
    )
  }
  return (
    <>
      {/* Top metrics */}
      <ErrorBoundary level="section">
        <ScalingMetrics strategies={strategies} decisions={decisions} signals={signals} />
      </ErrorBoundary>

      {/* Signal gauges and strategy controls side by side */}
      <div className="grid grid-cols-2 gap-grid-gap max-[1023px]:grid-cols-1">
        <ErrorBoundary level="section">
          <SignalGauges signals={signals} />
        </ErrorBoundary>
        <ErrorBoundary level="section">
          <StrategyControls strategies={strategies} />
        </ErrorBoundary>
      </div>

      {/* Recent decisions */}
      <ErrorBoundary level="section">
        <DecisionHistory decisions={decisions} />
      </ErrorBoundary>
    </>
  )
}
