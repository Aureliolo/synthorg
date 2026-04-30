import { useCallback, useMemo } from 'react'
import { useParams } from 'react-router'
import { createVersionHistoryClient } from '@/api/endpoints/version-history'
import { Breadcrumbs } from '@/components/ui/breadcrumbs'
import { DetailNavBar } from '@/components/ui/detail-nav-bar'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { VersionHistorySection } from '@/components/version-rollback/VersionHistorySection'
import { ROUTES } from '@/router/routes'
import { useAgentDetailData } from '@/hooks/useAgentDetailData'
import {
  useDetailNavigation,
  useDetailNavigationCallbacks,
} from '@/hooks/use-detail-navigation'
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

  // Build the version-history client lazily once per agent name.
  // The agent identity API is name-keyed (per the in-page note
  // above); ``agent.id`` is sometimes absent and using it would
  // either point at the wrong resource or disable history entirely.
  // The ``VersionHistorySection`` re-fetches when the client
  // identity changes, so memoising on the resolved name keeps the
  // page from hammering the endpoint on every render.
  const versionsClient = useMemo(
    () =>
      resolvedAgentName !== ''
        ? createVersionHistoryClient<Record<string, unknown>>(
            `/agents/${encodeURIComponent(resolvedAgentName)}`,
          )
        : null,
    [resolvedAgentName],
  )

  // Walk the company config's agent roster so prev/next on this
  // detail page steps through the same agents the AgentsPage shows.
  // The roster is shared state already in memory (no extra fetch),
  // and on a deep link without it the nav bar self-hides.
  const allAgents = useCompanyStore((s) => s.config?.agents) ?? []
  const routeForAgent = useCallback(
    (item: { id: string }) =>
      ROUTES.AGENT_DETAIL.replace(':agentId', encodeURIComponent(item.id)),
    [],
  )
  const navItems = useMemo(
    () => allAgents.map((a) => ({ id: a.id ?? a.name })),
    [allAgents],
  )
  const nav = useDetailNavigation({
    items: navItems,
    currentId: agentId,
    routeFor: routeForAgent,
  })
  const { goPrev, goNext } = useDetailNavigationCallbacks(nav)

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
      <div className="flex flex-wrap items-center gap-3">
        <Breadcrumbs items={[{ label: 'Agents', to: ROUTES.AGENTS }, { label: agent.name }]} />
        <DetailNavBar
          canPrev={nav.canPrev}
          canNext={nav.canNext}
          onPrev={goPrev}
          onNext={goNext}
          position={nav.position}
        />
      </div>

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

      {versionsClient !== null && (
        <ErrorBoundary level="section">
          <VersionHistorySection
            client={versionsClient}
            title="Version history"
            description="Each agent identity edit is captured as a snapshot. Select two versions to compare; select one to roll back."
            rollbackSupported
            emptyTitle="No identity versions yet"
            emptyDescription="Versions appear here after the first edit to this agent's identity."
          />
        </ErrorBoundary>
      )}
    </div>
  )
}
