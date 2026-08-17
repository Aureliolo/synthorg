import { lazy, Suspense, useMemo } from 'react'
import { MetricCard } from '@/components/ui/metric-card'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { SkeletonChart } from '@/components/ui/skeleton'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { PostSetupGuidanceCard } from '@/components/setup/PostSetupGuidanceCard'
import { useDashboardData } from '@/hooks/useDashboardData'
import { useDashboardPrefs } from '@/stores/dashboard-prefs'
import { computeMetricCards } from '@/utils/dashboard'
import { DashboardSkeleton } from './dashboard/DashboardSkeleton'
import { OrgHealthSection } from './dashboard/OrgHealthSection'
import { ActivityFeed } from './dashboard/ActivityFeed'
import { PendingApprovalsCard } from './dashboard/PendingApprovalsCard'
import { useApprovalsStore } from '@/stores/approvals'

const BudgetBurnChart = lazy(() =>
  import('./dashboard/BudgetBurnChart').then((m) => ({ default: m.BudgetBurnChart })),
)

type DashboardData = ReturnType<typeof useDashboardData>

function DashboardBudgetSection({
  overview,
  forecast,
  budgetConfig,
}: {
  overview: DashboardData['overview']
  forecast: DashboardData['forecast']
  budgetConfig: DashboardData['budgetConfig']
}) {
  return (
    <ErrorBoundary level="section">
      <Suspense fallback={<SkeletonChart />}>
        <BudgetBurnChart
          trendData={overview?.cost_7d_trend ?? []}
          forecast={forecast}
          budgetTotal={budgetConfig?.total_monthly ?? 0}
          budgetRemaining={overview?.budget_remaining}
          currency={overview?.currency}
        />
      </Suspense>
    </ErrorBoundary>
  )
}

export default function DashboardPage() {
  // The post-setup guidance card's dismissal is backend-owned (pure API
  // consumer). Gate on ``hydrated`` so it only appears once the backend
  // confirms it has not been dismissed (no flash for users who dismissed it).
  const guidanceDismissed = useDashboardPrefs((s) => s.postSetupGuidanceDismissed)
  const prefsHydrated = useDashboardPrefs((s) => s.hydrated)
  const dismissGuidance = useDashboardPrefs((s) => s.dismissPostSetupGuidance)

  const {
    overview,
    forecast,
    departmentHealths,
    departmentCount,
    activities,
    budgetConfig,
    orgHealthPercent,
    loading,
    error,
  } = useDashboardData()

  // The always-mounted sidebar badge owns the approvals fetch/poll/WS; read the
  // derived count + load state off the shared store via selectors only, so the
  // panel neither issues a second request nor flashes an empty state early.
  const pendingCount = useApprovalsStore(
    (s) => s.approvals.filter((a) => a.status === 'pending').length,
  )
  const approvalsLoading = useApprovalsStore((s) => s.loading)

  const metricCards = useMemo(
    () => (overview ? computeMetricCards(overview, budgetConfig) : []),
    [overview, budgetConfig],
  )

  if (loading && !overview) {
    return <DashboardSkeleton />
  }

  const showGuidance = prefsHydrated && !guidanceDismissed

  return (
    <div className="space-y-section-gap">
      {showGuidance && <PostSetupGuidanceCard onDismiss={dismissGuidance} />}

      {error && (
        <ErrorBanner severity="error" title="Could not load dashboard" description={error} />
      )}

      <StaggerGroup className="grid grid-cols-1 gap-grid-gap sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4">
        {metricCards.map((card) => (
          <StaggerItem key={card.label} className="h-full">
            <MetricCard {...card} className="h-full" />
          </StaggerItem>
        ))}
      </StaggerGroup>

      <div className="grid grid-cols-2 gap-grid-gap max-[1023px]:grid-cols-1">
        <ErrorBoundary level="section">
          <OrgHealthSection
            departments={departmentHealths}
            departmentCount={departmentCount}
            overallHealth={orgHealthPercent}
          />
        </ErrorBoundary>
        <div className="flex flex-col gap-grid-gap">
          <ErrorBoundary level="section">
            <ActivityFeed activities={activities} />
          </ErrorBoundary>
          <ErrorBoundary level="section">
            <PendingApprovalsCard count={pendingCount} loading={approvalsLoading} />
          </ErrorBoundary>
        </div>
      </div>

      <DashboardBudgetSection overview={overview} forecast={forecast} budgetConfig={budgetConfig} />
    </div>
  )
}
