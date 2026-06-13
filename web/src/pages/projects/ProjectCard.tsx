import { Link } from 'react-router'
import { Users } from 'lucide-react'
import { ROUTES } from '@/router/routes'
import { ProjectStatusBadge } from '@/components/ui/project-status-badge'
import { StatPill } from '@/components/ui/stat-pill'
import { cn } from '@/lib/utils'
import { formatCurrency, formatRelativeTime } from '@/utils/format'
import type { Project } from '@/api/types/projects'

interface ProjectCardProps {
  project: Project
  /** When defined, renders a selection checkbox overlay. */
  onToggleSelect?: ((id: string) => void) | undefined
  selected?: boolean | undefined
}

interface ProjectCardData {
  detailHref: string
  status: NonNullable<Project['status']> | 'planning'
  taskCount: number
  budget: number
  teamSize: number
}

function deriveProjectCardData(project: Project): ProjectCardData {
  return {
    detailHref: ROUTES.PROJECT_DETAIL.replace(':projectId', encodeURIComponent(project.id)),
    status: project.status,
    taskCount: project.task_ids.length,
    budget: project.budget,
    teamSize: project.team.length,
  }
}

function ProjectCardFooter({
  teamSize,
  deadline,
}: {
  teamSize: number
  deadline?: string | null
}) {
  return (
    <div className="flex items-center justify-between text-xs text-text-muted">
      <span className="flex items-center gap-1">
        <Users className="size-3" aria-hidden="true" />
        {teamSize} member{teamSize === 1 ? '' : 's'}
      </span>
      {deadline && <span>Due {formatRelativeTime(deadline)}</span>}
    </div>
  )
}

export function ProjectCard({ project, onToggleSelect, selected = false }: ProjectCardProps) {
  const { detailHref, status, taskCount, budget, teamSize } = deriveProjectCardData(project)
  return (
    <div className="relative">
      {onToggleSelect && (
        <label className="absolute left-3 top-3 z-10 flex cursor-pointer items-center">
          <input
            type="checkbox"
            className="size-4 rounded border-border accent-accent"
            checked={selected}
            onChange={() => onToggleSelect(project.id)}
            onClick={(e) => e.stopPropagation()}
            aria-label={`Select project ${project.name}`}
          />
        </label>
      )}
    <Link
      to={detailHref}
      className={cn(
        'block rounded-lg border bg-card p-card transition-shadow hover:shadow-[var(--so-shadow-card-hover)]',
        selected ? 'border-accent ring-2 ring-accent/30' : 'border-border',
        onToggleSelect && 'pl-8',
      )}
    >
      <div className="mb-2 flex items-center gap-2">
        <span className="truncate text-sm font-semibold text-foreground">{project.name}</span>
        <ProjectStatusBadge status={status} showLabel />
      </div>

      {project.description && (
        <p className="mb-3 line-clamp-2 text-xs text-muted-foreground">{project.description}</p>
      )}

      <div className="mb-2 flex flex-wrap items-center gap-2">
        <StatPill label="Tasks" value={taskCount} />
        {budget > 0 && <StatPill label="Budget" value={formatCurrency(budget)} />}
      </div>

      <ProjectCardFooter teamSize={teamSize} deadline={project.deadline} />
    </Link>
    </div>
  )
}
