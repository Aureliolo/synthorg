import { SectionCard } from '@/components/ui/section-card'
import { ProjectStatusBadge } from '@/components/ui/project-status-badge'
import { MetadataGrid } from '@/components/ui/metadata-grid'
import { UNASSIGNED_LABEL } from '@/utils/agents'
import { formatCurrency, formatDateTime, formatRelativeTime } from '@/utils/format'
import type { Project } from '@/api/types/projects'
import { ProjectDeleteAction } from './ProjectDeleteAction'

interface ProjectHeaderProps {
  project: Project
  /** Tasks fetched for this project; the source of the Tasks count. */
  taskCount: number
  /** Agents that took work on this initiative, derived from those tasks. */
  contributorCount: number
}

function buildProjectMetadata(project: Project, taskCount: number, contributorCount: number) {
  return [
    {
      label: 'Status',
      value: <ProjectStatusBadge status={project.status} showLabel />,
    },
    {
      label: 'Budget',
      value: formatCurrency(project.budget),
      valueClassName: 'font-mono text-xs',
    },
    {
      label: 'Deadline',
      // Absolute date with the relative "due in N days" on hover so this
      // matches the "Due" framing the project card uses in the list view.
      value: (
        <time dateTime={project.deadline ?? undefined} title={formatRelativeTime(project.deadline)}>
          {formatDateTime(project.deadline)}
        </time>
      ),
    },
    {
      label: 'Tasks',
      value: String(taskCount),
      valueClassName: 'font-mono text-xs',
    },
    {
      label: 'Contributors',
      value: String(contributorCount),
      valueClassName: 'font-mono text-xs',
    },
    {
      label: 'Lead',
      value: project.lead_name ?? UNASSIGNED_LABEL,
    },
  ]
}

export function ProjectHeader({ project, taskCount, contributorCount }: ProjectHeaderProps) {
  const metadataItems = buildProjectMetadata(project, taskCount, contributorCount)

  return (
    <SectionCard title={project.name} action={<ProjectDeleteAction project={project} />}>
      {project.description && (
        <p className="mb-4 text-sm text-muted-foreground">{project.description}</p>
      )}
      <MetadataGrid items={metadataItems} />
    </SectionCard>
  )
}
