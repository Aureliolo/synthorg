import { SectionCard } from '@/components/ui/section-card'
import { ProjectStatusBadge } from '@/components/ui/project-status-badge'
import { MetadataGrid } from '@/components/ui/metadata-grid'
import { formatCurrency, formatDate } from '@/utils/format'
import type { Project } from '@/api/types/projects'

interface ProjectHeaderProps {
  project: Project
}

export function ProjectHeader({ project }: ProjectHeaderProps) {
  const metadataItems = [
    {
      label: 'Status',
      value: <ProjectStatusBadge status={project.status ?? 'planning'} showLabel />,
    },
    {
      label: 'Budget',
      value: project.budget != null ? formatCurrency(project.budget) : '--',
      valueClassName: 'font-mono text-xs',
    },
    {
      label: 'Deadline',
      value: formatDate(project.deadline),
    },
    {
      label: 'Tasks',
      value: String(project.task_ids?.length ?? 0),
      valueClassName: 'font-mono text-xs',
    },
    {
      label: 'Team Size',
      value: String(project.team?.length ?? 0),
      valueClassName: 'font-mono text-xs',
    },
    {
      label: 'Lead',
      value: project.lead ?? '--',
    },
  ]

  return (
    <SectionCard title={project.name}>
      {project.description && (
        <p className="mb-4 text-sm text-muted-foreground">{project.description}</p>
      )}
      <MetadataGrid items={metadataItems} />
    </SectionCard>
  )
}
