import { Activity } from 'lucide-react'

import type { AgentActivity } from '@/api/types'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { MetricCard } from '@/components/ui/metric-card'
import { useMissionControlData } from '@/hooks/useMissionControlData'
import { useMissionControlStore } from '@/stores/mission-control'
import { cn } from '@/lib/utils'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { formatCurrency } from '@/utils/format'

const _PAUSE_REASON = 'Paused from mission control'
const _KILL_REASON = 'Killed from mission control'

function statusTone(activity: AgentActivity): string {
  if (activity.is_runaway) return 'text-danger'
  if (activity.is_stuck) return 'text-warning'
  return 'text-text-secondary'
}

function statusLabel(activity: AgentActivity): string {
  if (activity.is_runaway) return 'runaway'
  if (activity.is_stuck) return 'stuck'
  return 'healthy'
}

function statusText(activity: AgentActivity): string {
  if (activity.is_runaway) return 'runaway'
  if (activity.is_stuck) return 'stuck'
  return activity.status
}

function statusDotClass(activity: AgentActivity): string {
  if (activity.is_runaway) return 'bg-danger'
  if (activity.is_stuck) return 'bg-warning'
  return 'bg-success'
}

function AgentRowHeader({ activity, headerId }: { activity: AgentActivity; headerId: string }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="flex items-center gap-2">
        <span
          role="img"
          aria-label={`Status: ${statusLabel(activity)}`}
          className={cn('size-2 rounded-full', statusDotClass(activity))}
        />
        <span id={headerId} className="font-medium text-foreground">
          {activity.agent_id}
        </span>
        <span className="text-xs text-text-secondary">{activity.task_id}</span>
      </div>
      <div className="flex items-center gap-3 text-xs">
        <span className="text-text-secondary">turn {activity.turn_count}</span>
        <span className="font-mono text-foreground">
          {formatCurrency(activity.cost, DEFAULT_CURRENCY)}
        </span>
        <span className={cn('uppercase', statusTone(activity))}>{statusText(activity)}</span>
      </div>
    </div>
  )
}

function AgentRow({
  activity,
  onReplay,
}: {
  activity: AgentActivity
  onReplay: (executionId: string) => void
}) {
  const pause = useMissionControlStore((s) => s.pauseTaskAction)
  const kill = useMissionControlStore((s) => s.killTaskAction)
  const executionId = activity.execution_id

  return (
    <div className="rounded-lg border border-border bg-card p-card">
      <AgentRowHeader activity={activity} headerId={`agent-row-${activity.task_id}`} />

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() => void pause(activity.task_id, _PAUSE_REASON)}
        >
          Pause
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => void kill(activity.task_id, _KILL_REASON)}
        >
          Kill
        </Button>
        {executionId != null && (
          <Button variant="ghost" size="sm" onClick={() => onReplay(executionId)}>
            Replay
          </Button>
        )}
      </div>
    </div>
  )
}

interface CockpitMetrics {
  agents: readonly AgentActivity[]
  activeCount: number
  stuckCount: number
  runawayCount: number
  totalCost: number
}

function deriveCockpitMetrics(
  snapshot: ReturnType<typeof useMissionControlData>['snapshot'],
): CockpitMetrics {
  if (!snapshot) {
    return { agents: [], activeCount: 0, stuckCount: 0, runawayCount: 0, totalCost: 0 }
  }
  return {
    agents: snapshot.agents,
    activeCount: snapshot.active_count,
    stuckCount: snapshot.stuck_agents.length,
    runawayCount: snapshot.runaway_agents.length,
    totalCost: snapshot.total_cost,
  }
}

function CockpitMetricCards({ metrics }: { metrics: CockpitMetrics }) {
  return (
    <div className="grid grid-cols-2 gap-grid-gap lg:grid-cols-4">
      <MetricCard label="Active agents" value={metrics.activeCount} animateValue />
      <MetricCard
        label="Spend (active)"
        value={formatCurrency(metrics.totalCost, DEFAULT_CURRENCY)}
      />
      <MetricCard label="Stuck" value={metrics.stuckCount} animateValue />
      <MetricCard label="Runaway" value={metrics.runawayCount} animateValue />
    </div>
  )
}

function CockpitAgentList({
  agents,
  loading,
  onReplay,
}: {
  agents: readonly AgentActivity[]
  loading: boolean
  onReplay: (executionId: string) => void
}) {
  if (agents.length === 0) {
    return (
      <EmptyState
        icon={Activity}
        title={loading ? 'Loading activity...' : 'No active work'}
        description={
          loading
            ? 'Fetching the live org-activity snapshot.'
            : 'When the company is working, agents and their tasks appear here.'
        }
      />
    )
  }
  return (
    <div className="space-y-grid-gap">
      {agents.map((activity) => (
        <AgentRow key={activity.task_id} activity={activity} onReplay={onReplay} />
      ))}
    </div>
  )
}

export interface LiveCockpitProps {
  /** Switch to the flight recorder for an execution (deep-link from a row). */
  onReplay: (executionId: string) => void
}

export function LiveCockpit({ onReplay }: LiveCockpitProps) {
  const { snapshot, loading, error } = useMissionControlData()
  const metrics = deriveCockpitMetrics(snapshot)

  return (
    <div className="space-y-section-gap">
      {error != null && <ErrorBanner title="Failed to load activity" description={error} />}
      <CockpitMetricCards metrics={metrics} />
      <CockpitAgentList agents={metrics.agents} loading={loading} onReplay={onReplay} />
    </div>
  )
}
