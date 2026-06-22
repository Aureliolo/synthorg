import { memo } from 'react'
import { formatLabel } from '@/utils/format'
import { cn } from '@/lib/utils'
import type { TaskType } from '@/api/types/enums'
import type { Task } from '@/api/types/tasks'

interface TaskHistoryBarProps {
  task: Task
  maxDurationMs: number
}

const TYPE_COLORS: Record<TaskType, string> = {
  development: 'bg-accent',
  design: 'bg-success',
  research: 'bg-warning',
  review: 'bg-accent-dim',
  meeting: 'bg-muted-foreground',
  admin: 'bg-muted-foreground',
  analysis: 'bg-info',
}

/**
 * Parse created_at to epoch ms, returning 0 for a missing or non-empty-but-
 * invalid value. A NaN here would otherwise cascade into 'NaN%' bar widths and
 * malformed duration text, so every consumer reuses this guarded value rather
 * than re-parsing task.created_at.
 */
function safeCreatedMs(task: Task): number {
  if (!task.created_at) return 0
  const ms = new Date(task.created_at).getTime()
  return Number.isNaN(ms) ? 0 : ms
}

/** Parse updated_at safely; fall back to created_at if unparseable or earlier. */
function effectiveEndMs(task: Task): number {
  if (!task.created_at) return 0
  const createdMs = safeCreatedMs(task)
  if (createdMs === 0) return 0
  if (!task.updated_at) return createdMs
  const updatedMs = new Date(task.updated_at).getTime()
  if (Number.isNaN(updatedMs) || updatedMs < createdMs) return createdMs
  return updatedMs
}

function getBarWidth(task: Task, maxDurationMs: number): number {
  if (!task.created_at || maxDurationMs <= 0) return 20
  const durationMs = Math.max(0, effectiveEndMs(task) - safeCreatedMs(task))
  const pct = Math.max(20, Math.min(100, (durationMs / maxDurationMs) * 100))
  return pct
}

function formatDuration(task: Task): string {
  if (!task.created_at) return '--'
  const durationMs = Math.max(0, effectiveEndMs(task) - safeCreatedMs(task))
  const seconds = Math.floor(durationMs / 1000)
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  const mins = minutes % 60
  return `${hours}h ${mins}m`
}

function TaskHistoryBarImpl({ task, maxDurationMs }: TaskHistoryBarProps) {
  const isActive = task.status === 'in_progress'
  const barColor = TYPE_COLORS[task.type]
  const width = getBarWidth(task, maxDurationMs)

  return (
    <div className="flex items-center gap-3 py-1">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-xs font-sans text-foreground truncate">{task.title}</span>
          <span className="text-micro font-mono text-muted-foreground shrink-0">
            {formatLabel(task.type)}
          </span>
        </div>
        <div className="relative h-2 w-full rounded-full bg-border overflow-hidden">
          <div
            className={cn(
              'h-full rounded-full transition-all duration-[var(--so-transition-progress)]',
              barColor,
              isActive && 'animate-pulse',
            )}
            style={{ width: `${width}%` }}
          />
        </div>
      </div>
      <span className="text-micro font-mono text-muted-foreground shrink-0 w-12 text-right">
        {formatDuration(task)}
      </span>
    </div>
  )
}

export const TaskHistoryBar = memo(TaskHistoryBarImpl)
