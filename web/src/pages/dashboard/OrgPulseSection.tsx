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
  /**
   * Why each half cannot be trusted, when it cannot.
   *
   * Both halves state something positive about an empty list ("nothing is
   * running", "nothing is blocking progress"). Neither claim is safe to make
   * from an absence of data, so each needs to know its own read failed.
   */
  runningError: string | null
  blockersError: string | null
  runningLoading: boolean
  blockersLoading: boolean
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

/**
 * Severity in words as well as colour.
 *
 * The icon is decorative and the colour carries no meaning to a screen reader
 * or to a colour-blind operator, so a critical row and a warning row would
 * otherwise be indistinguishable.
 */
const SEVERITY_WORD = {
  critical: 'Critical',
  warning: 'Warning',
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
        <p className="text-sm text-foreground">
          <span className="sr-only">{SEVERITY_WORD[blocker.severity]}: </span>
          {blocker.title}
        </p>
        {blocker.detail !== null && (
          <p className="text-xs text-muted-foreground">{blocker.detail}</p>
        )}
        {blocker.href !== null && (
          // Named by what it goes to: a links-list rendered N identical "Go
          // there" entries, which is how a screen-reader user navigates.
          <Link
            to={blocker.href}
            aria-label={`Go to ${blocker.title}`}
            className="text-xs text-accent hover:underline"
          >
            Go there
          </Link>
        )}
      </div>
    </div>
  )
})

/**
 * What a half shows when its own read failed.
 *
 * Never the all-clear: "nothing is running" and "nothing is blocking progress"
 * are claims about the org, and an empty list caused by a failed fetch is not
 * evidence for either.
 */
function CouldNotRead({ what, detail }: { what: string; detail: string }) {
  return (
    <EmptyState
      icon={AlertTriangle}
      title={`Could not read ${what}`}
      description={detail}
    />
  )
}

function RunningNow({
  running,
  queue,
  runningError,
  runningLoading,
}: Pick<
  OrgPulseSectionProps,
  'running' | 'queue' | 'runningError' | 'runningLoading'
>) {
  const idle = queue.idleAgents === 1 ? '1 agent idle' : `${queue.idleAgents} agents idle`
  return (
    <div className="space-y-2">
      <h4 className="font-mono text-micro uppercase tracking-wide text-text-muted">
        Running now
      </h4>
      {runningError !== null ? (
        <CouldNotRead what="what is running" detail={runningError} />
      ) : runningLoading && running.length === 0 ? (
        <p className="text-sm text-muted-foreground">Reading the org's state...</p>
      ) : running.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nothing is running.</p>
      ) : (
        <StaggerGroup className="space-y-1.5">
          {running.map((activity) => (
            <StaggerItem key={activity.agent_id}>
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

function BlockingProgress({
  blockers,
  blockersError,
  blockersLoading,
}: Pick<OrgPulseSectionProps, 'blockers' | 'blockersError' | 'blockersLoading'>) {
  return (
    <div className="space-y-2 border-t border-border pt-3">
      <h4 className="font-mono text-micro uppercase tracking-wide text-text-muted">
        Blocking progress
      </h4>
      {blockersError !== null ? (
        <CouldNotRead what="what is blocking progress" detail={blockersError} />
      ) : blockersLoading && blockers.length === 0 ? (
        <p className="text-sm text-muted-foreground">Checking for blockers...</p>
      ) : blockers.length === 0 ? (
        <EmptyState
          icon={CheckCircle2}
          title="Nothing is blocking progress"
          description="Every declared subsystem is up or degraded-but-serving, no task is parked, and runs are producing output."
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
 * Health is a task-success rate, so a department that has run nothing reports
 * none, and a gauge reading 0% over a column of N/A answers neither question.
 * The per-department numbers live on the Org Chart's cards, which already
 * carry each department's agents, active count and 7-day cost.
 *
 * Each half owns its own loading and error state, because each is fed by a
 * different read and both make a positive claim when their list is empty.
 */
function OrgPulseSectionInner({
  running,
  queue,
  blockers,
  runningError,
  blockersError,
  runningLoading,
  blockersLoading,
}: OrgPulseSectionProps) {
  return (
    <SectionCard title="Org Pulse" icon={Activity}>
      <div className="space-y-3">
        <RunningNow
          running={running}
          queue={queue}
          runningError={runningError}
          runningLoading={runningLoading}
        />
        <BlockingProgress
          blockers={blockers}
          blockersError={blockersError}
          blockersLoading={blockersLoading}
        />
      </div>
    </SectionCard>
  )
}

export const OrgPulseSection = memo(OrgPulseSectionInner)
