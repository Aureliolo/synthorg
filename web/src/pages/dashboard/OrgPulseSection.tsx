import { memo } from 'react'
import { Link } from 'react-router'
import { Activity, AlertTriangle, CheckCircle2, Circle } from 'lucide-react'
import { SectionCard } from '@/components/ui/section-card'
import { EmptyState } from '@/components/ui/empty-state'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { cn } from '@/lib/utils'
import { UNKNOWN_AGENT_NAME } from '@/utils/agents'
import { formatRelativeTime } from '@/utils/format'
import type { Blocker, PulseQueue } from '@/utils/org-pulse'
import type { AgentActivity } from '@/api/types/cockpit'

export interface OrgPulseSectionProps {
  /** Work being executed right now, from the cockpit's live snapshot. */
  running: readonly AgentActivity[]
  queue: PulseQueue
  blockers: readonly Blocker[]
  loading: boolean
}

/** One task being worked, named by its title and whoever is on it. */
const RunningRow = memo(function RunningRow({ activity }: { activity: AgentActivity }) {
  const turns = activity.turn_count === 1 ? '1 turn' : `${activity.turn_count} turns`
  return (
    <div className="flex items-baseline justify-between gap-3">
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-foreground">
          {/* The backend resolves both, and sends null rather than the key it
              stands for, so the fallback wording is the dashboard's to choose. */}
          {activity.task_title ?? 'Untitled task'}
        </p>
        <p className="truncate text-xs text-muted-foreground">
          {activity.agent_name ?? UNKNOWN_AGENT_NAME}
          {' · '}
          {turns}
          {activity.last_active !== null && ` · ${formatRelativeTime(activity.last_active)}`}
        </p>
      </div>
      {activity.is_runaway && (
        <span className="shrink-0 text-xs font-medium text-danger">runaway</span>
      )}
      {!activity.is_runaway && activity.is_stuck && (
        <span className="shrink-0 text-xs font-medium text-warning">stuck</span>
      )}
    </div>
  )
})

const SEVERITY_ICON = {
  critical: AlertTriangle,
  warning: Circle,
} as const

const SEVERITY_CLASS = {
  critical: 'text-danger',
  warning: 'text-warning',
} as const

/** One thing standing between the org and progress, with its own reason. */
const BlockerRow = memo(function BlockerRow({ blocker }: { blocker: Blocker }) {
  const Icon = SEVERITY_ICON[blocker.severity]
  return (
    <div className="flex items-start gap-2">
      <Icon
        className={cn('mt-0.5 size-3.5 shrink-0', SEVERITY_CLASS[blocker.severity])}
        aria-hidden="true"
      />
      <div className="min-w-0 flex-1">
        <p className="text-sm text-foreground">{blocker.title}</p>
        {blocker.detail !== null && (
          <p className="text-xs text-muted-foreground">{blocker.detail}</p>
        )}
        {blocker.href !== null && (
          <Link to={blocker.href} className="text-xs text-accent hover:underline">
            Go there
          </Link>
        )}
      </div>
    </div>
  )
})

function RunningNow({
  running,
  queue,
}: Pick<OrgPulseSectionProps, 'running' | 'queue'>) {
  const idle = queue.idleAgents === 1 ? '1 agent idle' : `${queue.idleAgents} agents idle`
  return (
    <div className="space-y-2">
      <h4 className="font-mono text-micro uppercase tracking-wide text-text-muted">
        Running now
      </h4>
      {running.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nothing is running.</p>
      ) : (
        <StaggerGroup className="space-y-1.5">
          {running.map((activity) => (
            <StaggerItem key={activity.task_id}>
              <RunningRow activity={activity} />
            </StaggerItem>
          ))}
        </StaggerGroup>
      )}
      <p className="font-mono text-xs text-text-secondary">
        {queue.queued} queued · {idle}
      </p>
    </div>
  )
}

function BlockingProgress({ blockers }: Pick<OrgPulseSectionProps, 'blockers'>) {
  return (
    <div className="space-y-2 border-t border-border pt-3">
      <h4 className="font-mono text-micro uppercase tracking-wide text-text-muted">
        Blocking progress
      </h4>
      {blockers.length === 0 ? (
        <EmptyState
          icon={CheckCircle2}
          title="Nothing is blocking progress"
          description="Every declared subsystem is up, no task is parked, and runs are producing output."
        />
      ) : (
        <StaggerGroup className="space-y-2">
          {blockers.map((blocker) => (
            <StaggerItem key={blocker.id}>
              <BlockerRow blocker={blocker} />
            </StaggerItem>
          ))}
        </StaggerGroup>
      )}
    </div>
  )
}

/**
 * What the org is doing, and what is stopping it.
 *
 * Replaces a health panel that could not answer either. Health is a task-success
 * rate, so a department that has run nothing reports none, and a fresh org saw a
 * 0% gauge over a column of N/A. The per-department numbers are not relocated
 * here: the Org Chart's cards already carry each department's agents, active
 * count and 7-day cost.
 */
function OrgPulseSectionInner({
  running,
  queue,
  blockers,
  loading,
}: OrgPulseSectionProps) {
  return (
    <SectionCard title="Org Pulse" icon={Activity}>
      {loading && running.length === 0 && blockers.length === 0 ? (
        <p className="text-sm text-muted-foreground">Reading the org's state…</p>
      ) : (
        <div className="space-y-3">
          <RunningNow running={running} queue={queue} />
          <BlockingProgress blockers={blockers} />
        </div>
      )}
    </SectionCard>
  )
}

export const OrgPulseSection = memo(OrgPulseSectionInner)
