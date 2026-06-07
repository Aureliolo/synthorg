import { useParams } from 'react-router'
import { Breadcrumbs } from '@/components/ui/breadcrumbs'
import { DetailNavBar } from '@/components/ui/detail-nav-bar'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { VersionHistorySection } from '@/components/version-rollback/VersionHistorySection'
import { ROUTES } from '@/router/routes'
import { AgentDetailSkeleton } from './agents/AgentDetailSkeleton'
import { AgentIdentityHeader } from './agents/AgentIdentityHeader'
import { ProseInsight } from './agents/ProseInsight'
import { PerformanceMetrics } from './agents/PerformanceMetrics'
import { ToolBadges } from './agents/ToolBadges'
import { CareerTimeline } from './agents/CareerTimeline'
import { TaskHistory } from './agents/TaskHistory'
import { ActivityLog } from './agents/ActivityLog'
import { QualityScoreOverride } from './agents/QualityScoreOverride'
import { CollaborationPanel } from './agents/CollaborationPanel'
import { TrainingSection } from './agents/TrainingSection'
import {
  useAgentDetailPageController,
  type AgentDetailPageController,
} from './agents/useAgentDetailPageController'

export default function AgentDetailPage() {
  // URLs use the agent's stable ID (or name as a fallback). Display names can
  // contain arbitrary characters; the id is URL-safe by construction.
  const { agentId } = useParams<{ agentId: string }>()
  const ctrl = useAgentDetailPageController(agentId)
  const { data } = ctrl
  const { agent, error } = data

  if (error && !agent) {
    return (
      <div className="space-y-section-gap">
        <Breadcrumbs
          items={[
            { label: 'Agents', to: ROUTES.AGENTS },
            { label: ctrl.resolvedAgentName || 'Unknown agent' },
          ]}
        />
        <ErrorBanner severity="error" title="Agent not found" description={error} />
      </div>
    )
  }

  if (!agent) return <AgentDetailSkeleton />

  return (
    <div className="space-y-section-gap">
      <AgentDetailBreadcrumbsRow agent={agent} ctrl={ctrl} />
      <AgentDetailBanners ctrl={ctrl} />
      <AgentDetailContent ctrl={ctrl} />
    </div>
  )
}

interface CtrlProps {
  ctrl: AgentDetailPageController
}

function AgentDetailBreadcrumbsRow({
  agent,
  ctrl,
}: CtrlProps & { agent: NonNullable<AgentDetailPageController['data']['agent']> }) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <Breadcrumbs
        items={[{ label: 'Agents', to: ROUTES.AGENTS }, { label: agent.name }]}
      />
      <DetailNavBar
        canPrev={ctrl.nav.canPrev}
        canNext={ctrl.nav.canNext}
        onPrev={ctrl.goPrev}
        onNext={ctrl.goNext}
        position={ctrl.nav.position}
      />
    </div>
  )
}

function AgentDetailBanners({ ctrl }: CtrlProps) {
  const { error, wsConnected, loading, wsSetupError } = ctrl.data
  return (
    <>
      {error && (
        <ErrorBanner
          severity="error"
          title="Could not load agent data"
          description={error}
        />
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

function AgentDetailContent({ ctrl }: CtrlProps) {
  const { agent, performanceCards, insights, agentTasks, activity, activityTotal, careerHistory, fetchMoreActivity } =
    ctrl.data
  if (!agent) return null
  const allowedTools = extractAllowedTools(agent.tools['allowed'])

  return (
    <>
      <ErrorBoundary level="section">
        <AgentIdentityHeader agent={agent} />
      </ErrorBoundary>
      <ErrorBoundary level="section">
        <ProseInsight insights={insights} />
      </ErrorBoundary>
      <ErrorBoundary level="section">
        <PerformanceMetrics cards={performanceCards} />
      </ErrorBoundary>
      <ErrorBoundary level="section">
        <ToolBadges tools={allowedTools} />
      </ErrorBoundary>
      <ErrorBoundary level="section">
        <QualityScoreOverride agentId={agent.id} />
      </ErrorBoundary>
      <ErrorBoundary level="section">
        <CollaborationPanel agentId={agent.id} />
      </ErrorBoundary>
      <ErrorBoundary level="section">
        <TrainingSection agentId={agent.id} />
      </ErrorBoundary>
      <div className="grid grid-cols-2 gap-grid-gap max-[1023px]:grid-cols-1">
        <ErrorBoundary level="section">
          <CareerTimeline events={careerHistory} />
        </ErrorBoundary>
        <ErrorBoundary level="section">
          <TaskHistory tasks={agentTasks} />
        </ErrorBoundary>
      </div>
      <ErrorBoundary level="section">
        <ActivityLog
          events={activity}
          total={activityTotal}
          onLoadMore={fetchMoreActivity}
        />
      </ErrorBoundary>
      {ctrl.versionsClient !== null && (
        <ErrorBoundary level="section">
          <VersionHistorySection
            client={ctrl.versionsClient}
            title="Version history"
            description="Each agent identity edit is captured as a snapshot. Select two versions to compare; select one to roll back."
            rollbackSupported
            emptyTitle="No identity versions yet"
            emptyDescription="Versions appear here after the first edit to this agent's identity."
          />
        </ErrorBoundary>
      )}
    </>
  )
}

function extractAllowedTools(allowed: unknown): readonly string[] {
  if (!Array.isArray(allowed)) return []
  return (allowed as unknown[]).filter((t): t is string => typeof t === 'string')
}
