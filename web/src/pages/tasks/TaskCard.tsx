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
}

export function TaskCard({ task, onSelect, isDragging, isOverlay, className, ref, ...props }: TaskCardProps) {
  const { triggerFlash, flashStyle } = useFlash()
  const prevUpdatedRef = useRef(task.updated_at)

  useEffect(() => {
    if (task.updated_at && task.updated_at !== prevUpdatedRef.current) {
      triggerFlash()
    }
    prevUpdatedRef.current = task.updated_at
  }, [task.updated_at, triggerFlash])

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
      className={cn(
        'cursor-pointer rounded-lg border border-border bg-card p-card transition-colors',
        'hover:border-border-bright hover:bg-card-hover hover:-translate-y-px hover:shadow-[var(--so-shadow-card-hover)]',
        FOCUS_RING,
        // Elevated shadow while drag/overlay is active -- same token the
        // hover state uses, keeps shadow weight consistent across states.
        isDragging && 'scale-[1.02] opacity-50 shadow-[var(--so-shadow-card-hover)]',
        isOverlay && 'scale-[1.02] shadow-[var(--so-shadow-card-hover)] border-accent/50',
        className,
      )}
      {...props}
    >
      {/* Header: title + status */}
      <div className="flex items-start justify-between gap-2">
        <h3 className="line-clamp-2 text-[13px] font-semibold text-foreground">
          {task.title}
        </h3>
        <TaskStatusIndicator status={task.status} className="mt-0.5" />
      </div>

      {/* Description preview */}
      {task.description && (
        <p className="mt-1 line-clamp-2 text-xs text-text-secondary">
          {task.description}
        </p>
      )}

      {/* Footer: priority, assignee, metadata */}
      <div className="mt-2 flex items-center gap-2">
        <PriorityBadge priority={task.priority} />

        {task.assigned_to && (
          <Avatar name={task.assigned_to} size="sm" />
        )}

        <div className="ml-auto flex items-center gap-2 text-text-muted">
          {task.dependencies.length > 0 && (
            <span className="flex items-center gap-0.5 text-[10px] font-mono" title={`${task.dependencies.length} dependencies`}>
              <GitBranch className="size-3" aria-hidden="true" />
              {task.dependencies.length}
            </span>
          )}

          {task.cost != null && task.cost > 0 && (
            <span className="text-[10px] font-mono">
              {formatCurrency(task.cost, DEFAULT_CURRENCY)}
            </span>
          )}

          {task.deadline && (
            <span className="flex items-center gap-0.5 text-[10px] font-mono" title={`Deadline: ${task.deadline}`}>
              <Clock className="size-3" aria-hidden="true" />
              {formatRelativeTime(task.deadline)}
            </span>
          )}
        </div>
      </div>
    </div>
  )
}
