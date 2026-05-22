import { Activity } from 'lucide-react'
import { useState } from 'react'

import type { AgentActivity } from '@/api/types'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { InputField } from '@/components/ui/input-field'
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

function AgentRow({ activity }: { activity: AgentActivity }) {
  const [hint, setHint] = useState('')
  const pause = useMissionControlStore((s) => s.pauseTaskAction)
  const kill = useMissionControlStore((s) => s.killTaskAction)
  const sendHint = useMissionControlStore((s) => s.sendHintAction)

  return (
    <div className="rounded-lg border border-border bg-card p-card">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span
            className={cn(
              'size-2 rounded-full',
              activity.is_runaway
                ? 'bg-danger'
                : activity.is_stuck
                  ? 'bg-warning'
                  : 'bg-success',
            )}
          />
          <span className="font-medium text-foreground">{activity.agent_id}</span>
          <span className="text-xs text-text-secondary">{activity.task_id}</span>
        </div>
        <div className="flex items-center gap-3 text-xs">
          <span className="text-text-secondary">turn {activity.turn_count}</span>
          <span className="font-mono text-foreground">
            {formatCurrency(activity.cost, DEFAULT_CURRENCY)}
          </span>
          <span className={cn('uppercase', statusTone(activity))}>
            {activity.is_runaway
              ? 'runaway'
              : activity.is_stuck
                ? 'stuck'
                : activity.status}
          </span>
        </div>
      </div>

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
        {activity.execution_id != null && (
          <div className="flex items-center gap-2">
            <InputField
              label="Hint"
              placeholder="Hint or redirect..."
              value={hint}
              onChange={(e) => setHint(e.target.value)}
            />
            <Button
              variant="default"
              size="sm"
              disabled={hint.trim() === ''}
              onClick={() => {
                const executionId = activity.execution_id
                if (executionId == null || hint.trim() === '') return
                void sendHint(executionId, activity.agent_id, hint.trim())
                setHint('')
              }}
            >
              Send
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}

export function LiveCockpit() {
  const { snapshot, loading, error } = useMissionControlData()

  const agents = snapshot?.agents ?? []
  const activeCount = snapshot?.active_count ?? 0
  const stuckCount = snapshot?.stuck_agents.length ?? 0
  const runawayCount = snapshot?.runaway_agents.length ?? 0
  const totalCost = snapshot?.total_cost ?? 0

  return (
    <div className="space-y-section-gap">
      {error != null && <ErrorBanner title="Failed to load activity" description={error} />}

      <div className="grid grid-cols-2 gap-grid-gap lg:grid-cols-4">
        <MetricCard label="Active agents" value={activeCount} animateValue />
        <MetricCard
          label="Spend (active)"
          value={formatCurrency(totalCost, DEFAULT_CURRENCY)}
        />
        <MetricCard label="Stuck" value={stuckCount} animateValue />
        <MetricCard label="Runaway" value={runawayCount} animateValue />
      </div>

      {agents.length === 0 ? (
        <EmptyState
          icon={Activity}
          title={loading ? 'Loading activity...' : 'No active work'}
          description={
            loading
              ? 'Fetching the live org-activity snapshot.'
              : 'When the company is working, agents and their tasks appear here.'
          }
        />
      ) : (
        <div className="space-y-grid-gap">
          {agents.map((activity) => (
            <AgentRow key={activity.task_id} activity={activity} />
          ))}
        </div>
      )}
    </div>
  )
}
