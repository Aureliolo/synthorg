import { lazy, Suspense, useMemo } from 'react'
import { Calendar } from 'lucide-react'
import { Breadcrumbs } from '@/components/ui/breadcrumbs'
import { MetricCard } from '@/components/ui/metric-card'
import { SectionCard } from '@/components/ui/section-card'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { EmptyState } from '@/components/ui/empty-state'
import { SkeletonCard, SkeletonChart, SkeletonMetric, SkeletonTable } from '@/components/ui/skeleton'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { useBudgetData } from '@/hooks/useBudgetData'
import { ROUTES } from '@/router/routes'
import { formatCurrency } from '@/utils/format'
import { computeExhaustionDate, type BudgetMetricCardData } from '@/utils/budget'
import type { ForecastPoint } from '@/api/types/analytics'

const SpendBurnChart = lazy(() =>
  import('./budget/SpendBurnChart').then((m) => ({ default: m.SpendBurnChart })),
)

function ProjectionRow({ point, cumulative, currency, totalMonthly }: {
  point: ForecastPoint
  cumulative: number
  currency?: string
  totalMonthly: number
}) {
  const budgetPct = totalMonthly > 0 ? (cumulative / totalMonthly) * 100 : 0
  return (
    <tr>
      <td className="px-4 py-2 font-mono text-xs text-foreground">{point.day}</td>
      <td className="min-w-[7ch] px-4 py-2 text-right font-mono text-xs text-text-secondary">
        {formatCurrency(point.projected_spend, currency)}
      </td>
      <td className="min-w-[7ch] px-4 py-2 text-right font-mono text-xs text-text-secondary">
        {formatCurrency(cumulative, currency)}
      </td>
      <td className="min-w-[6ch] px-4 py-2 text-right font-mono text-xs text-text-muted">
        {budgetPct.toFixed(1)}%
      </td>
    </tr>
  )
}

type ForecastData = ReturnType<typeof useBudgetData>['forecast']

function useCumulativeValues(forecast: ForecastData): number[] {
  return useMemo(() => {
    if (!forecast) return []
    let running = 0
    return forecast.daily_projections.map((p) => {
      running += p.projected_spend
      return running
    })
  }, [forecast])
}

function useForecastMetricCards(
  forecast: ForecastData,
  currency: string | undefined,
): BudgetMetricCardData[] {
  return useMemo((): BudgetMetricCardData[] => {
    if (!forecast) return []
    return [
      {
        label: 'PROJECTED TOTAL',
        value: formatCurrency(forecast.projected_total, currency),
      },
      {
        label: 'DAYS UNTIL EXHAUSTED',
        value: forecast.days_until_exhausted != null
          ? String(forecast.days_until_exhausted)
          : 'N/A',
        subText: computeExhaustionDate(forecast.days_until_exhausted ?? null) ?? undefined,
      },
      {
        label: 'CONFIDENCE',
        value: Number.isFinite(forecast.confidence) ? `${Math.round(forecast.confidence * 100)}%` : '--',
      },
      {
        label: 'AVG DAILY SPEND',
        value: formatCurrency(forecast.avg_daily_spend, currency),
      },
    ]
  }, [forecast, currency])
}

function ForecastLoadingSkeleton() {
  return (
    <div className="space-y-section-gap" role="status" aria-live="polite" aria-label="Loading forecast">
      <div className="grid grid-cols-4 gap-grid-gap max-[1023px]:grid-cols-2">
        <SkeletonMetric />
        <SkeletonMetric />
        <SkeletonMetric />
        <SkeletonMetric />
      </div>
      <SkeletonCard header lines={3} />
      <SkeletonTable rows={7} columns={4} />
    </div>
  )
}

function ForecastBanners({
  error,
  wsConnected,
  loading,
  wsSetupError,
}: {
  error: string | null
  wsConnected: boolean
  loading: boolean
  wsSetupError: string | null
}) {
  return (
    <>
      {error && (
        <ErrorBanner severity="error" title="Could not load budget forecast" description={error} />
      )}
      {!wsConnected && !loading && (
        <ErrorBanner
          variant="offline"
          title="Real-time updates disconnected"
          description={wsSetupError ?? 'Data may be stale until the connection recovers.'}
        />
      )}
    </>
  )
}

interface DailyProjectionsProps {
  forecast: ForecastData
  cumulativeValues: readonly number[]
  currency: string | undefined
  totalMonthly: number
  showEmpty: boolean
}

function DailyProjections({
  forecast,
  cumulativeValues,
  currency,
  totalMonthly,
  showEmpty,
}: DailyProjectionsProps) {
  if (forecast && forecast.daily_projections.length > 0) {
    return (
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full">
          <thead>
            <tr className="border-b border-border bg-surface">
              <th scope="col" className="px-4 py-2 text-left text-[11px] font-semibold uppercase tracking-wider text-text-muted">Day</th>
              <th scope="col" className="min-w-[7ch] px-4 py-2 text-right text-[11px] font-semibold uppercase tracking-wider text-text-muted">Projected Spend</th>
              <th scope="col" className="min-w-[7ch] px-4 py-2 text-right text-[11px] font-semibold uppercase tracking-wider text-text-muted">Cumulative</th>
              <th scope="col" className="min-w-[6ch] px-4 py-2 text-right text-[11px] font-semibold uppercase tracking-wider text-text-muted">% of Budget</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {forecast.daily_projections.map((point, idx) => (
              <ProjectionRow
                key={point.day}
                point={point}
                cumulative={cumulativeValues[idx] ?? 0}
                currency={currency}
                totalMonthly={totalMonthly}
              />
            ))}
          </tbody>
        </table>
      </div>
    )
  }
  if (showEmpty) {
    return (
      <EmptyState
        icon={Calendar}
        title="No forecast data"
        description="Forecast projections will appear once enough spending data is available"
      />
    )
  }
  return null
}

export default function BudgetForecastPage() {
  const { overview, budgetConfig, forecast, trends, loading, error, wsConnected, wsSetupError } =
    useBudgetData()

  const currency = overview?.currency ?? budgetConfig?.currency
  const cumulativeValues = useCumulativeValues(forecast)
  const metricCards = useForecastMetricCards(forecast, currency)

  if (loading && !overview) {
    return <ForecastLoadingSkeleton />
  }

  return (
    <div className="space-y-section-gap">
      <Breadcrumbs
        items={[
          { label: 'Budget', to: ROUTES.BUDGET },
          { label: 'Forecast' },
        ]}
      />
      <h1 className="text-lg font-semibold text-foreground">Budget Forecast</h1>

      <ForecastBanners
        error={error}
        wsConnected={wsConnected}
        loading={loading}
        wsSetupError={wsSetupError}
      />

      <StaggerGroup className="grid grid-cols-4 gap-grid-gap max-[1023px]:grid-cols-2">
        {metricCards.map((card) => (
          <StaggerItem key={card.label}>
            <MetricCard {...card} />
          </StaggerItem>
        ))}
      </StaggerGroup>

      <ErrorBoundary level="section">
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
      </ErrorBoundary>

      <SectionCard title="Daily Projections" icon={Calendar}>
        <DailyProjections
          forecast={forecast}
          cumulativeValues={cumulativeValues}
          currency={currency}
          totalMonthly={budgetConfig?.total_monthly ?? 0}
          showEmpty={!error}
        />
      </SectionCard>
    </div>
  )
}
