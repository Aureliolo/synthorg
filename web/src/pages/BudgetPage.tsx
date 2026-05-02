import { lazy, Suspense, useMemo, useState } from 'react'
import { budgetConfigVersionsClient } from '@/api/endpoints/version-history'
import { MetricCard } from '@/components/ui/metric-card'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { ListHeader } from '@/components/ui/list-header'
import { SkeletonChart } from '@/components/ui/skeleton'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { VersionHistorySection } from '@/components/version-rollback/VersionHistorySection'
import { useBudgetData } from '@/hooks/useBudgetData'
import {
  computeAgentSpending,
  computeBudgetMetricCards,
  computeCategoryBreakdown,
  computeCostBreakdown,
  filterCfoEvents,
  getThresholdZone,
  type BreakdownDimension,
} from '@/utils/budget'
import { BudgetSkeleton } from './budget/BudgetSkeleton'
import { BudgetGauge } from './budget/BudgetGauge'

// Lazy-loaded recharts wrappers: defers the ~150 KB recharts bundle
// to first chart render so the BudgetPage's initial entry chunk
// stays small. Vite splits a dedicated chunk via the dynamic
// imports below.
const SpendBurnChart = lazy(() =>
  import('./budget/SpendBurnChart').then((m) => ({ default: m.SpendBurnChart })),
)
const CostBreakdownChart = lazy(() =>
  import('./budget/CostBreakdownChart').then((m) => ({ default: m.CostBreakdownChart })),
)
import { CategoryBreakdown } from './budget/CategoryBreakdown'
import { AgentSpendingTable } from './budget/AgentSpendingTable'
import { CfoActivityFeed } from './budget/CfoActivityFeed'
import { PeriodSelector } from './budget/PeriodSelector'
import { ThresholdAlerts } from './budget/ThresholdAlerts'

export default function BudgetPage() {
  const {
    overview,
    budgetConfig,
    forecast,
    costRecords,
    trends,
    activities,
    agentNameMap,
    agentDeptMap,
    aggregationPeriod,
    setAggregationPeriod,
    loading,
    error,
    wsConnected,
    wsSetupError,
  } = useBudgetData()

  const [breakdownDimension, setBreakdownDimension] = useState<BreakdownDimension>('agent')

  const currency = overview?.currency ?? budgetConfig?.currency

  const thresholdZone = useMemo(
    () =>
      overview && budgetConfig
        ? getThresholdZone(overview.budget_used_percent, budgetConfig.alerts)
        : 'normal' as const,
    [overview, budgetConfig],
  )

  const metricCards = useMemo(
    () => (overview ? computeBudgetMetricCards(overview, budgetConfig, forecast) : []),
    [overview, budgetConfig, forecast],
  )

  const agentSpendingRows = useMemo(
    () => computeAgentSpending(costRecords, budgetConfig?.total_monthly ?? 0, agentNameMap),
    [costRecords, budgetConfig, agentNameMap],
  )

  const costBreakdown = useMemo(
    () => computeCostBreakdown(costRecords, breakdownDimension, agentNameMap, agentDeptMap),
    [costRecords, breakdownDimension, agentNameMap, agentDeptMap],
  )

  const categoryRatio = useMemo(
    () => computeCategoryBreakdown(costRecords),
    [costRecords],
  )

  const cfoEvents = useMemo(
    () => filterCfoEvents(activities),
    [activities],
  )

  if (loading && !overview) {
    return <BudgetSkeleton />
  }

  return (
    <div className="space-y-section-gap">
      <ListHeader
        title="Budget"
        primaryAction={
          <PeriodSelector value={aggregationPeriod} onChange={setAggregationPeriod} />
        }
      />

      {error && (
        <ErrorBanner severity="error" title="Could not load budget" description={error} />
      )}

      {!wsConnected && !loading && (
        <ErrorBanner
          variant="offline"
          title="Real-time updates disconnected"
          description={wsSetupError ?? 'Data may be stale until the connection recovers.'}
        />
      )}

      <ThresholdAlerts zone={thresholdZone} budgetConfig={budgetConfig} overview={overview} />

      <StaggerGroup className="grid grid-cols-4 gap-grid-gap max-[1023px]:grid-cols-2">
        {metricCards.map((card) => (
          <StaggerItem key={card.label}>
            <MetricCard {...card} />
          </StaggerItem>
        ))}
      </StaggerGroup>

      <div className="grid grid-cols-3 gap-grid-gap max-[1023px]:grid-cols-1">
        <ErrorBoundary level="section">
          <BudgetGauge
            usedPercent={overview?.budget_used_percent ?? 0}
            budgetRemaining={overview?.budget_remaining ?? 0}
            daysUntilExhausted={forecast?.days_until_exhausted ?? null}
            currency={currency}
          />
        </ErrorBoundary>
        <ErrorBoundary level="section">
          <div className="col-span-2 max-[1023px]:col-span-1">
            <Suspense fallback={<SkeletonChart />}>
              <SpendBurnChart
                trendData={trends?.data_points ?? []}
                forecast={forecast}
                budgetTotal={budgetConfig?.total_monthly ?? 0}
                budgetRemaining={overview?.budget_remaining}
                alerts={budgetConfig?.alerts}
                currency={currency}
              />
            </Suspense>
          </div>
        </ErrorBoundary>
      </div>

      <div className="grid grid-cols-2 gap-grid-gap max-[1023px]:grid-cols-1">
        <ErrorBoundary level="section">
          <Suspense fallback={<SkeletonChart aspectRatio={1} />}>
            <CostBreakdownChart
              breakdown={costBreakdown}
              dimension={breakdownDimension}
              onDimensionChange={setBreakdownDimension}
              deptDisabled={agentDeptMap.size === 0}
              currency={currency}
            />
          </Suspense>
        </ErrorBoundary>
        <ErrorBoundary level="section">
          <CategoryBreakdown ratio={categoryRatio} currency={currency} />
        </ErrorBoundary>
      </div>

      <ErrorBoundary level="section">
        <AgentSpendingTable rows={agentSpendingRows} currency={currency} />
      </ErrorBoundary>

      <ErrorBoundary level="section">
        <CfoActivityFeed events={cfoEvents} />
      </ErrorBoundary>

      <ErrorBoundary level="section">
        <VersionHistorySection
          client={budgetConfigVersionsClient}
          title="Budget config history"
          description="Read-only audit trail of budget configuration snapshots. Select two versions to compare."
          emptyTitle="No budget config versions yet"
          emptyDescription="Versions appear here after the first edit to the budget configuration."
        />
      </ErrorBoundary>
    </div>
  )
}
