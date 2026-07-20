import { Link } from 'react-router'
import { GitBranch } from 'lucide-react'
import { EmptyState } from '@/components/ui/empty-state'
import { PlanStatusBadge } from '@/components/ui/plan-status-badge'
import { ProgressIndicator } from '@/components/ui/progress-indicator'
import { SectionCard } from '@/components/ui/section-card'
import { StatPill } from '@/components/ui/stat-pill'
import { StatusPill } from '@/components/ui/status-pill'
import { TaskStatusIndicator } from '@/components/ui/task-status-indicator'
import { ROUTES } from '@/router/routes'
import { cn } from '@/lib/utils'
import type { ProjectProgress, ProjectProgressItem } from '@/api/types/projects'

const PERCENT_MAX = 100

interface ProjectPlanProgressProps {
  progress: ProjectProgress | null
  /**
   * True when the progress fetch failed rather than the project having no
   * plan. The store only sets this alongside null progress, so it is read
   * solely on the empty path.
   */
  failed?: boolean
}

function percentComplete(done: number, total: number): number {
  if (total === 0) return 0
  return Math.round((done / total) * PERCENT_MAX)
}

function ItemStatus({ item }: { item: ProjectProgressItem }) {
  if (item.kind === 'decision') {
    return (
      <StatusPill tone={item.done ? 'success' : 'warning'}>
        {item.done ? 'Decided' : 'Undecided'}
      </StatusPill>
    )
  }
  if (item.task_status) {
    return <TaskStatusIndicator status={item.task_status} label />
  }
  return <StatusPill tone="text-secondary">Not dispatched</StatusPill>
}

function ItemRow({ item }: { item: ProjectProgressItem }) {
  return (
    <li
      className={cn(
        'flex items-center gap-3 rounded-md border p-card',
        item.on_critical_path ? 'border-accent/50 bg-accent/5' : 'border-border',
      )}
    >
      <span className="min-w-0 flex-1 truncate text-sm text-foreground">
        {item.title}
      </span>
      {item.on_critical_path && (
        <span
          className="flex items-center gap-1 text-xs text-accent"
          title="On the critical path"
        >
          <GitBranch className="size-3" aria-hidden="true" />
          Critical
        </span>
      )}
      {item.owner && (
        <span className="hidden text-xs text-text-muted sm:inline">{item.owner}</span>
      )}
      <ItemStatus item={item} />
      {item.task_id && (
        <Link
          to={ROUTES.TASK_DETAIL.replace(':taskId', encodeURIComponent(item.task_id))}
          className="text-xs text-accent hover:underline"
          aria-label={`View task for ${item.title}`}
        >
          Task
        </Link>
      )}
    </li>
  )
}

// A failed fetch and a plan-less project both arrive as null progress, but
// claiming "no plan yet" when the request simply failed tells the operator
// something untrue about their initiative.
function EmptyProgress({ failed }: { failed: boolean }) {
  return (
    <SectionCard title="Plan progress">
      {failed ? (
        <EmptyState
          title="Progress unavailable"
          description="The plan progress for this initiative could not be loaded. It will reappear once the request succeeds."
        />
      ) : (
        <EmptyState
          title="No plan yet"
          description="This initiative has no approved plan. Once a plan is approved and dispatched, its items and their progress appear here."
        />
      )}
    </SectionCard>
  )
}

function ProgressSummaryRow({ progress }: { progress: ProjectProgress }) {
  const { counts } = progress
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2">
      {progress.plan_status && <PlanStatusBadge status={progress.plan_status} />}
      <StatPill label="Done" value={`${String(counts.done)}/${String(counts.total)}`} />
      {counts.failed > 0 && (
        <StatusPill tone="danger">{`${String(counts.failed)} failed`}</StatusPill>
      )}
      {counts.blocked > 0 && (
        <StatusPill tone="warning">{`${String(counts.blocked)} blocked`}</StatusPill>
      )}
    </div>
  )
}

export function ProjectPlanProgress({ progress, failed = false }: ProjectPlanProgressProps) {
  if (!progress || progress.plan_id === null) {
    return <EmptyProgress failed={failed} />
  }

  const { counts, items } = progress
  const percent = percentComplete(counts.done, counts.total)

  return (
    <SectionCard title="Plan progress">
      <ProgressSummaryRow progress={progress} />

      {progress.objective_title && (
        <p className="mb-3 text-sm text-muted-foreground">{progress.objective_title}</p>
      )}

      <ProgressIndicator
        variant="determinate"
        value={percent}
        label="Items complete"
        description={`${String(percent)}% of plan items are done`}
        className="mb-4"
      />

      <ul role="list" className="flex flex-col gap-2">
        {items.map((item) => (
          <ItemRow key={item.item_id} item={item} />
        ))}
      </ul>
    </SectionCard>
  )
}
