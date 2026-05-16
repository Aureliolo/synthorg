import { Button } from '@/components/ui/button'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { ListHeader } from '@/components/ui/list-header'
import { useScalingData } from '@/hooks/useScalingData'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'

const log = createLogger('ScalingPage')

import { DecisionHistory } from './scaling/DecisionHistory'
import { ScalingMetrics } from './scaling/ScalingMetrics'
import { ScalingSkeleton } from './scaling/ScalingSkeleton'
import { SignalGauges } from './scaling/SignalGauges'
import { StrategyControls } from './scaling/StrategyControls'

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

  const addToast = useToastStore((s) => s.add)

  const handleEvaluateNow = async () => {
    try {
      const results = await evaluateNow()
      if (results.length > 0) {
        addToast({
          variant: 'success',
          title: `Evaluation produced ${results.length} decision(s)`,
        })
      } else {
        addToast({
          variant: 'info',
          title: 'Evaluation produced no decisions',
        })
      }
    } catch (err) {
      log.error('Evaluation failed', err)
      addToast({
        variant: 'error',
        title: 'Evaluation failed',
      })
    }
  }

  if (loading && strategies.length === 0) {
    return <ScalingSkeleton />
  }

  return (
    <div className="flex flex-col gap-section-gap">
      <ListHeader
        title="Dynamic Scaling"
        refreshing={isRefetching}
        primaryAction={
          <Button onClick={handleEvaluateNow} disabled={evaluating}>
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

      {/* Top metrics */}
      <ErrorBoundary level="section">
        <ScalingMetrics
          strategies={strategies}
          decisions={decisions}
          signals={signals}
        />
      </ErrorBoundary>

      {/* Signal gauges and strategy controls side by side */}
      <div className="grid grid-cols-2 gap-grid-gap">
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
    </div>
  )
}
