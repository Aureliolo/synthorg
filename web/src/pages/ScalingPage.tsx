import { Scale } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { ListHeader } from '@/components/ui/list-header'
import { ROUTES } from '@/router/routes'
import { useScalingData } from '@/hooks/useScalingData'

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

  if (loading && strategies.length === 0) {
    return <ScalingSkeleton />
  }

  return (
    <div className="flex flex-col gap-section-gap">
      <ListHeader
        title="Dynamic Scaling"
        refreshing={isRefetching}
        primaryAction={
          <Button
            size="sm"
            onClick={() => {
              void evaluateNow()
            }}
            disabled={evaluating}
          >
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
