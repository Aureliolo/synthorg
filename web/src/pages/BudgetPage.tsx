import { lazy, Suspense, useState } from 'react'
import { budgetConfigVersionsClient } from '@/api/endpoints/version-history'
import type { ParetoFrontier } from '@/api/types'
import { MetricCard } from '@/components/ui/metric-card'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { ListHeader } from '@/components/ui/list-header'
import { SkeletonChart } from '@/components/ui/skeleton'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { VersionHistorySection } from '@/components/version-rollback/VersionHistorySection'
import { useBudgetData } from '@/hooks/useBudgetData'
import { type BreakdownDimension } from '@/utils/budget'
import {
  useBudgetDerived,
  useForecastActions,
  useParetoFrontier,
  type BudgetData,
  type BudgetDerived,
  type CurrentForecast,
  type ForecastActions,
} from './budget/budget-page-data'
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
import { CostForecastApprovalCard } from '@/components/approvals/CostForecastApprovalCard'
import { CategoryBreakdown } from './budget/CategoryBreakdown'
import { HardCeilingHaltedBanner } from './budget/HardCeilingHaltedBanner'
import { AgentSpendingTable } from './budget/AgentSpendingTable'
import { BudgetForecastDialog } from './budget/BudgetForecastDialog'
import { CfoActivityFeed } from './budget/CfoActivityFeed'
import { ParetoSection } from './budget/ParetoSection'
import { PeriodSelector } from './budget/PeriodSelector'
import { ThresholdAlerts } from './budget/ThresholdAlerts'
import { useBudgetForecastStore } from '@/stores/budgetForecast'

function BudgetBanners({
  data,
  thresholdZone,
}: {
  data: BudgetData
  thresholdZone: BudgetDerived['thresholdZone']
}) {
  const { error, wsConnected, loading, wsSetupError, budgetConfig, overview } = data
  return (
    <>
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
    </>
  )
}

function ForecastBanners({
  currentForecast,
  actions,
  onOpenDetail,
}: {
  currentForecast: CurrentForecast
  actions: ForecastActions
  onOpenDetail: () => void
}) {
  if (currentForecast === null) return null
  const { forecastMutating, approveForecast, rejectForecast, raiseCeiling } = actions

  if (currentForecast.halt_context !== null) {
    const halt = currentForecast.halt_context
    return (
      <HardCeilingHaltedBanner
        accumulatedCost={halt.accumulated_cost}
        ceilingAmount={halt.ceiling_amount}
        currency={halt.currency}
        forecastId={currentForecast.forecast_id}
        mutating={forecastMutating}
        onRaiseCeiling={(newCeiling) => {
          void raiseCeiling(currentForecast.forecast_id, {
            new_ceiling: newCeiling,
            accumulated_cost: halt.accumulated_cost,
          })
        }}
      />
    )
  }

  if (currentForecast.decision === 'pending') {
    return (
      <CostForecastApprovalCard
        forecast={currentForecast}
        mutating={forecastMutating}
        onApprove={(ceiling) => {
          void approveForecast(currentForecast.forecast_id, {
            decided_by: 'operator',
            ceiling_amount: ceiling,
          })
        }}
        onReject={() => {
          void rejectForecast(currentForecast.forecast_id, { decided_by: 'operator' })
        }}
        onOpenDetail={onOpenDetail}
      />
    )
  }

  return null
}

function BudgetMetricCards({ metricCards }: { metricCards: BudgetDerived['metricCards'] }) {
  return (
    <StaggerGroup className="grid grid-cols-4 gap-grid-gap max-[1279px]:grid-cols-3 max-[1023px]:grid-cols-2">
      {metricCards.map((card) => (
        <StaggerItem key={card.label}>
          <MetricCard {...card} />
        </StaggerItem>
      ))}
    </StaggerGroup>
  )
}

interface BudgetChartsProps {
  data: BudgetData
  derived: BudgetDerived
  breakdownDimension: BreakdownDimension
  onDimensionChange: (dimension: BreakdownDimension) => void
  paretoFrontier: ParetoFrontier | null
  paretoLoading: boolean
}

function BudgetGaugeCell({ data, currency }: { data: BudgetData; currency: string | undefined }) {
  const { overview, forecast } = data
  return (
    <ErrorBoundary level="section">
      <BudgetGauge
        usedPercent={overview?.budget_used_percent ?? 0}
        budgetRemaining={overview?.budget_remaining ?? 0}
        daysUntilExhausted={forecast?.days_until_exhausted ?? null}
        currency={currency}
      />
    </ErrorBoundary>
  )
}

function SpendBurnCell({ data, currency }: { data: BudgetData; currency: string | undefined }) {
  const { overview, budgetConfig, forecast, trends } = data
  return (
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
  )
}

function BudgetGaugeRow({ data, currency }: { data: BudgetData; currency: string | undefined }) {
  return (
    <div className="grid grid-cols-3 gap-grid-gap max-[1023px]:grid-cols-2 max-[767px]:grid-cols-1">
      <BudgetGaugeCell data={data} currency={currency} />
      <SpendBurnCell data={data} currency={currency} />
    </div>
  )
}

function BudgetBreakdownRow({
  data,
  derived,
  breakdownDimension,
  onDimensionChange,
}: {
  data: BudgetData
  derived: BudgetDerived
  breakdownDimension: BreakdownDimension
  onDimensionChange: (dimension: BreakdownDimension) => void
}) {
  const { currency, costBreakdown, categoryRatio } = derived
  return (
    <div className="grid grid-cols-2 gap-grid-gap max-[1023px]:grid-cols-1">
      <ErrorBoundary level="section">
        <Suspense fallback={<SkeletonChart aspectRatio={1} />}>
          <CostBreakdownChart
            breakdown={costBreakdown}
            dimension={breakdownDimension}
            onDimensionChange={onDimensionChange}
            deptDisabled={data.agentDeptMap.size === 0}
            currency={currency}
          />
        </Suspense>
      </ErrorBoundary>
      <ErrorBoundary level="section">
        <CategoryBreakdown ratio={categoryRatio} currency={currency} />
      </ErrorBoundary>
    </div>
  )
}

function BudgetCharts({
  data,
  derived,
  breakdownDimension,
  onDimensionChange,
  paretoFrontier,
  paretoLoading,
}: BudgetChartsProps) {
  const { currency, agentSpendingRows, cfoEvents } = derived
  return (
    <>
      <BudgetGaugeRow data={data} currency={currency} />
      <BudgetBreakdownRow
        data={data}
        derived={derived}
        breakdownDimension={breakdownDimension}
        onDimensionChange={onDimensionChange}
      />

      <ErrorBoundary level="section">
        <ParetoSection frontier={paretoFrontier} loading={paretoLoading} />
      </ErrorBoundary>

      <ErrorBoundary level="section">
        <AgentSpendingTable rows={agentSpendingRows} currency={currency} />
      </ErrorBoundary>

      <ErrorBoundary level="section">
        <CfoActivityFeed events={cfoEvents} />
      </ErrorBoundary>
    </>
  )
}

function BudgetForecastDetailDialog({
  open,
  currentForecast,
  actions,
  onOpenChange,
}: {
  open: boolean
  currentForecast: CurrentForecast
  actions: ForecastActions
  onOpenChange: (open: boolean) => void
}) {
  const { forecastMutating, approveForecast, rejectForecast } = actions
  return (
    <BudgetForecastDialog
      open={open && currentForecast !== null}
      onOpenChange={onOpenChange}
      forecast={currentForecast}
      mutating={forecastMutating}
      onApprove={(ceiling) => {
        if (currentForecast !== null) {
          void approveForecast(currentForecast.forecast_id, {
            decided_by: 'operator',
            ceiling_amount: ceiling,
          })
          onOpenChange(false)
        }
      }}
      onReject={() => {
        if (currentForecast !== null) {
          void rejectForecast(currentForecast.forecast_id, { decided_by: 'operator' })
          onOpenChange(false)
        }
      }}
    />
  )
}

function BudgetVersionHistory() {
  return (
    <ErrorBoundary level="section">
      <VersionHistorySection
        client={budgetConfigVersionsClient}
        title="Budget config history"
        description="Read-only audit trail of budget configuration snapshots. Select two versions to compare."
        emptyTitle="No budget config versions yet"
        emptyDescription="Versions appear here after the first edit to the budget configuration."
      />
    </ErrorBoundary>
  )
}

export default function BudgetPage() {
  const data = useBudgetData()
  const [breakdownDimension, setBreakdownDimension] = useState<BreakdownDimension>('agent')
  const [forecastDialogOpen, setForecastDialogOpen] = useState(false)
  const { paretoFrontier, paretoLoading } = useParetoFrontier()
  const derived = useBudgetDerived(data, breakdownDimension)

  const currentForecast = useBudgetForecastStore((s) => s.current)
  const forecastActions = useForecastActions()

  if (data.loading && !data.overview) {
    return <BudgetSkeleton />
  }

  return (
    <div className="space-y-section-gap">
      <ListHeader
        title="Budget"
        description="Live spend, burn-rate forecast, and cost breakdowns."
        refreshing={data.isRefetching}
        primaryAction={
          <PeriodSelector value={data.aggregationPeriod} onChange={data.setAggregationPeriod} />
        }
      />

      <BudgetBanners data={data} thresholdZone={derived.thresholdZone} />

      <ForecastBanners
        currentForecast={currentForecast}
        actions={forecastActions}
        onOpenDetail={() => setForecastDialogOpen(true)}
      />

      <BudgetMetricCards metricCards={derived.metricCards} />

      <BudgetCharts
        data={data}
        derived={derived}
        breakdownDimension={breakdownDimension}
        onDimensionChange={setBreakdownDimension}
        paretoFrontier={paretoFrontier}
        paretoLoading={paretoLoading}
      />

      <BudgetForecastDetailDialog
        open={forecastDialogOpen}
        currentForecast={currentForecast}
        actions={forecastActions}
        onOpenChange={setForecastDialogOpen}
      />

      <BudgetVersionHistory />
    </div>
  )
}
