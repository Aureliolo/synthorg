import { useParams } from 'react-router'
import { Breadcrumbs } from '@/components/ui/breadcrumbs'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { ROUTES } from '@/router/routes'
import { useAgentDetailData } from '@/hooks/useAgentDetailData'
import { useCompanyStore } from '@/stores/company'
import { AgentDetailSkeleton } from './agents/AgentDetailSkeleton'
import { AgentIdentityHeader } from './agents/AgentIdentityHeader'
import { ProseInsight } from './agents/ProseInsight'
import { PerformanceMetrics } from './agents/PerformanceMetrics'
import { ToolBadges } from './agents/ToolBadges'
import { CareerTimeline } from './agents/CareerTimeline'
import { TaskHistory } from './agents/TaskHistory'
import { ActivityLog } from './agents/ActivityLog'
import { QualityScoreOverride } from './agents/QualityScoreOverride'
import { TrainingSection } from './agents/TrainingSection'

export default function AgentDetailPage() {
  // URLs use the agent's stable ID (or name as a fallback when an
  // agent has no explicit id), NOT the display name.  Display names
  // can contain arbitrary characters (unicode, quotes, slashes)
  // and URL-encoding them produced failed backend lookups because
  // of case/trim normalisation quirks.  The id is URL-safe by
  // construction.  We resolve it back to the agent's name for the
  // data hook since the backend API is still name-keyed.
  const { agentId } = useParams<{ agentId: string }>()

  const configAgent = useCompanyStore((s) =>
    s.config?.agents.find((a) => (a.id ?? a.name) === agentId),
  )
  const resolvedAgentName = configAgent?.name ?? agentId ?? ''

  const {
    agent,
    performanceCards,
    insights,
    agentTasks,
    activity,
    activityTotal,
    careerHistory,
    loading,
    error,
    wsConnected,
    wsSetupError,
    fetchMoreActivity,
  } = useAgentDetailData(resolvedAgentName)

  if (loading && !agent) {
    return <AgentDetailSkeleton />
  }

  const allowedTools = agent
    ? (Array.isArray(agent.tools['allowed'])
        ? (agent.tools['allowed'] as unknown[]).filter((t): t is string => typeof t === 'string')
        : [])
    : []

  if (!agent) {
    return (
      <div className="space-y-section-gap">
        <Breadcrumbs items={[{ label: 'Agents', to: ROUTES.AGENTS }, { label: resolvedAgentName || 'Unknown agent' }]} />
        <ErrorBanner severity="error" title="Agent not found" description={error ?? undefined} />
      </div>
    )
  }

  return (
    <div className="space-y-section-gap">
      <Breadcrumbs items={[{ label: 'Agents', to: ROUTES.AGENTS }, { label: agent.name }]} />

      {error && (
        <ErrorBanner severity="error" title="Could not load agent data" description={error} />
      )}

      {!wsConnected && !loading && (
        <ErrorBanner
          variant="offline"
          title="Real-time updates disconnected"
          description={wsSetupError ?? 'Data may be stale until the connection recovers.'}
        />
      )}

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
        {agent.id && <QualityScoreOverride agentId={agent.id} />}
      </ErrorBoundary>

      <ErrorBoundary level="section">
        <TrainingSection agentName={agent.name} />
      </ErrorBoundary>

      <div className="grid grid-cols-2 gap-grid-gap max-[1023px]:grid-cols-1">
        <ErrorBoundary level="section">
          <CareerTimeline events={[...careerHistory]} />
        </ErrorBoundary>
        <ErrorBoundary level="section">
          <TaskHistory tasks={agentTasks} />
        </ErrorBoundary>
      </div>

      <ErrorBoundary level="section">
        <ActivityLog
          events={[...activity]}
          total={activityTotal}
          onLoadMore={fetchMoreActivity}
        />
      </ErrorBoundary>
    </div>
  )
}
