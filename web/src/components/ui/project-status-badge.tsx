import { cn } from '@/lib/utils'
import type { ProjectStatus } from '@/api/types/enums'

const STATUS_LABELS: Record<ProjectStatus, string> = {
  planning: 'Planning',
  active: 'Active',
  integrating: 'Integrating',
  evaluating: 'Evaluating',
  on_hold: 'On Hold',
  completed: 'Completed',
  cancelled: 'Cancelled',
}

const DOT_COLOR_CLASSES: Record<ProjectStatus, string> = {
  planning: 'bg-text-secondary',
  active: 'bg-accent',
  integrating: 'bg-accent',
  evaluating: 'bg-accent',
  on_hold: 'bg-warning',
  completed: 'bg-success',
  cancelled: 'bg-danger',
}

export interface ProjectStatusBadgeProps {
  status: ProjectStatus
  /**
   * Defaults on. Three in-flight statuses (active, integrating, evaluating)
   * share the `accent` tone, because the palette has one colour for "in
   * flight" and inventing another would leave the design system, so the label
   * is what tells them apart. Turn it off only where the surrounding text
   * already names the status.
   */
  showLabel?: boolean
  className?: string
}

export function ProjectStatusBadge({ status, showLabel = true, className }: ProjectStatusBadgeProps) {
  const label = STATUS_LABELS[status]

  return (
    <span
      className={cn('inline-flex items-center gap-1.5', className)}
      aria-label={label}
    >
      <span
        data-slot="status-dot"
        className={cn('size-1.5 shrink-0 rounded-full', DOT_COLOR_CLASSES[status])}
      />
      {showLabel && (
        <span className="text-xs text-text-secondary">{label}</span>
      )}
    </span>
  )
}
