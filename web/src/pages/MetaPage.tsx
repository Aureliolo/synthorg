import {
  Brain,
  ClipboardList,
  Dna,
  FlaskConical,
  MessageCircle,
  Settings2,
  Shield,
  Users,
  Zap,
} from 'lucide-react'

import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { MetricCard } from '@/components/ui/metric-card'
import { SectionCard } from '@/components/ui/section-card'
import { WsConnectionBanner } from '@/components/ui/ws-connection-banner'
import { SkeletonCard, SkeletonMetric } from '@/components/ui/skeleton'
import { useMetaData } from '@/hooks/useMetaData'

import { ExperimentExplorer } from './meta/ExperimentExplorer'
import { MetaABTestView } from './meta/MetaABTestView'
import { MetaAct } from './meta/MetaAct'
import { MetaChat } from './meta/MetaChat'
import { MetaGroup } from './meta/MetaGroup'
import { MetaPropose } from './meta/MetaPropose'
import { MetaProposalList } from './meta/MetaProposalList'
import { MetaRuleStatus } from './meta/MetaRuleStatus'
import { MetaEvolutionView } from './meta/MetaEvolutionView'
import { MetaSignalOverview } from './meta/MetaSignalOverview'

type MetaPageMode = 'loading' | 'error' | 'disabled' | 'ready'

function deriveMetaPageMode(
  config: ReturnType<typeof useMetaData>['config'],
  loading: boolean,
  error: string | null,
): MetaPageMode {
  if (loading && config === null) return 'loading'
  if (error && config === null) return 'error'
  if (!config?.enabled) return 'disabled'
  return 'ready'
}

export default function MetaPage() {
  const data = useMetaData()
  const mode = deriveMetaPageMode(data.config, data.loading, data.error)

  if (mode === 'loading') return <MetaLoadingSkeleton />
  if (mode === 'error') {
    return (
      <div className="mx-auto max-w-7xl p-card">
        <EmptyState
          icon={Brain}
          title="Failed to Load"
          description={`Could not load meta-loop data: ${data.error}`}
        />
      </div>
    )
  }
  if (mode === 'disabled') {
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
  return <MetaPageReady data={data} />
}

interface MetaPageReadyProps {
  data: ReturnType<typeof useMetaData>
}

function MetaPageReady({ data }: MetaPageReadyProps) {
  const { config, proposals, abTests, evolutionSummary, evolutionAxes, signals } =
    data
  const pendingCount = proposals.filter((p) => p.status === 'pending').length
  const activeRollouts = proposals.filter((p) => p.status === 'approved').length
  return (
    <ErrorBoundary level="page">
      <div className="mx-auto max-w-7xl space-y-section-gap p-card">
        <MetaPageHeader />
        <WsConnectionBanner description="Meta-loop signals may be stale until the connection recovers." />
        <MetaMetricsRow
          pendingCount={pendingCount}
          activeRollouts={activeRollouts}
          signalDomains={signals?.domains.length ?? 0}
        />
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
        <SectionCard title="Agent Evolution" icon={Dna}>
          <MetaEvolutionView summary={evolutionSummary} axes={evolutionAxes} />
        </SectionCard>
        <SectionCard title="Experiment Registry" icon={FlaskConical}>
          <ExperimentExplorer />
        </SectionCard>
        <SectionCard title="Improvement Proposals" icon={Brain}>
          <MetaProposalList proposals={proposals} />
        </SectionCard>
        {config?.chief_of_staff_enabled && (
          <>
            <SectionCard title="Chief of Staff" icon={MessageCircle}>
              <MetaChat />
            </SectionCard>
            <SectionCard title="Conversational Intake" icon={ClipboardList}>
              <MetaPropose />
            </SectionCard>
            <SectionCard title="Group Chat" icon={Users}>
              <MetaGroup />
            </SectionCard>
            <SectionCard title="Direct Action" icon={Zap}>
              <MetaAct />
            </SectionCard>
          </>
        )}
      </div>
    </ErrorBoundary>
  )
}

function MetaLoadingSkeleton() {
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

function MetaPageHeader() {
  return (
    <header className="flex items-center justify-between">
      <div>
        <h1 className="text-2xl font-semibold text-foreground">
          Company Self-Improvement
        </h1>
        <p className="text-sm text-muted-foreground">
          Meta-loop signals, proposals, and rollout status
        </p>
      </div>
    </header>
  )
}

interface MetaMetricsRowProps {
  pendingCount: number
  activeRollouts: number
  signalDomains: number
}

function MetaMetricsRow({
  pendingCount,
  activeRollouts,
  signalDomains,
}: MetaMetricsRowProps) {
  return (
    <div className="grid grid-cols-1 gap-grid-gap md:grid-cols-3">
      <MetricCard label="Pending Proposals" value={pendingCount} />
      <MetricCard label="Active Rollouts" value={activeRollouts} />
      <MetricCard label="Signal Domains" value={signalDomains} />
    </div>
  )
}
