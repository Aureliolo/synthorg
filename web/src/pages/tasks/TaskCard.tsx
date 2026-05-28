import { useEffect, useRef, type Ref } from 'react'
import { Clock, GitBranch } from 'lucide-react'
import { cn, FOCUS_RING } from '@/lib/utils'
import { Avatar } from '@/components/ui/avatar'
import { TaskStatusIndicator } from '@/components/ui/task-status-indicator'
import { PriorityBadge } from '@/components/ui/task-status-indicator'
import { useFlash } from '@/hooks/useFlash'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { formatRelativeTime, formatCurrency } from '@/utils/format'
import type { DashboardTask } from '@/api/types/tasks'

export interface TaskCardProps {
  task: DashboardTask
  onSelect: (taskId: string) => void
  isDragging?: boolean
  isOverlay?: boolean
  className?: string
  ref?: Ref<HTMLDivElement>
  /** Currency code for cost display (e.g. ``'USD'``). Pass the active
   * tenant / user / workspace currency to override the framework-wide
   * default. Optional so existing callers keep working; new code paths
   * should thread the active currency through explicitly per the
   * regional-defaults policy (no privileged region/currency in framework
   * code). */
  currency?: string
}

export function TaskCard({
  task,
  onSelect,
  isDragging,
  isOverlay,
  className,
  ref,
  currency,
  ...props
}: TaskCardProps) {
  const { triggerFlash, flashStyle } = useFlash()
  const prevUpdatedRef = useRef(task.updated_at)
  useEffect(() => {
    if (task.updated_at && task.updated_at !== prevUpdatedRef.current) {
      triggerFlash()
    }
    prevUpdatedRef.current = task.updated_at
  }, [task.updated_at, triggerFlash])

  const cardClasses = cn(
    'cursor-pointer rounded-lg border border-border bg-card p-card transition-colors',
    'hover:border-border-bright hover:bg-card-hover hover:-translate-y-px hover:shadow-[var(--so-shadow-card-hover)]',
    FOCUS_RING,
    isDragging && 'scale-[1.02] opacity-50 shadow-[var(--so-shadow-card-hover)]',
    isOverlay && 'scale-[1.02] shadow-[var(--so-shadow-card-hover)] border-accent/50',
    className,
  )
  return (
    <div
      ref={ref}
      role="button"
      tabIndex={0}
      aria-label={`Task: ${task.title}`}
      aria-roledescription="draggable task"
      data-dragging={isDragging ? 'true' : undefined}
      onClick={() => onSelect(task.id)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onSelect(task.id)
        }
      }}
      style={flashStyle}
      className={cardClasses}
      {...props}
    >
      <TaskCardHeader title={task.title} status={task.status} />
      {task.description && (
        <p className="mt-1 line-clamp-2 text-xs text-text-secondary">{task.description}</p>
      )}
      <TaskCardFooter task={task} currency={currency} />
    </div>
  )
}

interface TaskCardHeaderProps {
  title: string
  status: DashboardTask['status']
}

function TaskCardHeader({ title, status }: TaskCardHeaderProps) {
  return (
    <div className="flex items-start justify-between gap-2">
      <h3 className="line-clamp-2 text-[13px] font-semibold text-foreground">{title}</h3>
      <TaskStatusIndicator status={status} className="mt-0.5" />
    </div>
  )
}

interface TaskCardFooterProps {
  task: DashboardTask
  currency?: string
}

function TaskCardFooter({ task, currency }: TaskCardFooterProps) {
  const showCost = task.cost != null && task.cost > 0
  // ``currency ?? DEFAULT_CURRENCY``: callers that thread the active
  // tenant/user currency win; legacy callers fall back to the framework
  // default. The hardcoded reference is still here as a fallback only,
  // not as the privileged source.
  const displayCurrency = currency ?? DEFAULT_CURRENCY
  return (
    <div className="mt-2 flex items-center gap-2">
      <PriorityBadge priority={task.priority} />
      {task.assigned_to && <Avatar name={task.assigned_to} size="sm" />}
      <div className="ml-auto flex items-center gap-2 text-text-muted">
        {task.dependencies.length > 0 && (
          <span
            className="flex items-center gap-0.5 text-[10px] font-mono"
            title={`${task.dependencies.length} dependencies`}
          >
            <GitBranch className="size-3" aria-hidden="true" />
            {task.dependencies.length}
          </span>
        )}
        {showCost && (
          <span className="text-[10px] font-mono">
            {formatCurrency(task.cost!, displayCurrency)}
          </span>
        )}
        {task.deadline && (
          <span
            className="flex items-center gap-0.5 text-[10px] font-mono"
            title={`Deadline: ${task.deadline}`}
          >
            <Clock className="size-3" aria-hidden="true" />
            {formatRelativeTime(task.deadline)}
          </span>
        )}
      </div>
    </div>
  )
}
