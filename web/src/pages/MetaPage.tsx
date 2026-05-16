import {
  Brain,
  FlaskConical,
  MessageCircle,
  RefreshCw,
  Settings2,
  Shield,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { MetricCard } from '@/components/ui/metric-card'
import { SectionCard } from '@/components/ui/section-card'
import { WsConnectionBanner } from '@/components/ui/ws-connection-banner'
import {
  SkeletonCard,
  SkeletonMetric,
} from '@/components/ui/skeleton'
import { useMetaData } from '@/hooks/useMetaData'

import { MetaABTestView } from './meta/MetaABTestView'
import { MetaChat } from './meta/MetaChat'
import { MetaProposalList } from './meta/MetaProposalList'
import { MetaRuleStatus } from './meta/MetaRuleStatus'
import { MetaSignalOverview } from './meta/MetaSignalOverview'

export default function MetaPage() {
  const { config, proposals, abTests, signals, loading, error } =
    useMetaData()

  if (loading && config === null) {
    return (
      <div className="mx-auto max-w-7xl space-y-section-gap p-card">
        <div className="grid grid-cols-1 gap-grid-gap md:grid-cols-3">
          <SkeletonMetric />
          <SkeletonMetric />
          <SkeletonMetric />
        </div>
        <div className="grid grid-cols-1 gap-grid-gap lg:grid-cols-2">
          <SkeletonCard />
          <SkeletonCard />
        </div>
      </div>
    )
  }

  if (error && config === null) {
    return (
      <div className="mx-auto max-w-7xl p-card">
        <EmptyState
          icon={Brain}
          title="Failed to Load"
          description={`Could not load meta-loop data: ${error}`}
        />
      </div>
    )
  }

  const enabled = config?.enabled ?? false

  if (!enabled) {
    return (
      <div className="mx-auto max-w-7xl p-card">
        <EmptyState
          icon={Brain}
          title="Self-Improvement Disabled"
          description="Enable the self-improvement meta-loop in your company configuration to see improvement proposals, org signals, and rollout status."
        />
      </div>
    )
  }

  const pendingCount = proposals.filter(
    (p) => p.status === 'pending',
  ).length
  const activeRollouts = proposals.filter(
    (p) => p.status === 'approved',
  ).length

  return (
    <ErrorBoundary level="page">
      <div className="mx-auto max-w-7xl space-y-section-gap p-card">
        <header className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-foreground">
              Company Self-Improvement
            </h1>
            <p className="text-sm text-muted-foreground">
              Meta-loop signals, proposals, and rollout status
            </p>
          </div>
          <Button variant="outline" size="sm" disabled={loading}>
            <RefreshCw className="mr-2 h-4 w-4" />
            Trigger Cycle
          </Button>
        </header>

        <WsConnectionBanner description="Meta-loop signals may be stale until the connection recovers." />

        <div className="grid grid-cols-1 gap-grid-gap md:grid-cols-3">
          <MetricCard label="Pending Proposals" value={pendingCount} />
          <MetricCard
            label="Active Rollouts"
            value={activeRollouts}
          />
          <MetricCard
            label="Signal Domains"
            value={signals?.domains.length ?? 0}
          />
        </div>

        <div className="grid grid-cols-1 gap-grid-gap lg:grid-cols-2">
          <SectionCard title="Signal Overview" icon={Settings2}>
            <MetaSignalOverview signals={signals} />
          </SectionCard>

          <SectionCard title="Rule Status" icon={Shield}>
            <MetaRuleStatus />
          </SectionCard>
        </div>

        <SectionCard title="A/B Tests" icon={FlaskConical}>
          <MetaABTestView tests={abTests} />
        </SectionCard>

        <SectionCard title="Improvement Proposals" icon={Brain}>
          <MetaProposalList proposals={proposals} />
        </SectionCard>

        {config?.chief_of_staff_enabled && (
          <SectionCard title="Chief of Staff" icon={MessageCircle}>
            <MetaChat />
          </SectionCard>
        )}
      </div>
    </ErrorBoundary>
  )
}
