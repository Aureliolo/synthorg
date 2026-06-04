import { Link } from 'react-router'
import { Users } from 'lucide-react'
import { SectionCard } from '@/components/ui/section-card'
import { Avatar } from '@/components/ui/avatar'
import { EmptyState } from '@/components/ui/empty-state'
import { ROUTES } from '@/router/routes'
import type { Project } from '@/api/types/projects'

interface ProjectTeamSectionProps {
  project: Project
}

function TeamMemberRow({ agentId, isLead }: { agentId: string; isLead: boolean }) {
  return (
    <Link
      to={ROUTES.AGENT_DETAIL.replace(':agentId', encodeURIComponent(agentId))}
      className="flex items-center gap-2 rounded-md px-2 py-1.5 transition-colors hover:bg-accent/5"
    >
      <Avatar name={agentId} />
      <span className="text-sm text-foreground">{agentId}</span>
      {isLead && (
        <span className="ml-auto rounded-sm bg-accent/10 px-1.5 py-0.5 text-[10px] font-medium uppercase text-accent">
          Lead
        </span>
      )}
    </Link>
  )
}

export function ProjectTeamSection({ project }: ProjectTeamSectionProps) {
  const team = project.team
  if (team.length === 0) {
    return (
      <SectionCard title="Team" icon={Users}>
        <EmptyState
          icon={Users}
          title="No team members"
          description="This project has no assigned team members."
        />
      </SectionCard>
    )
  }

  return (
    <SectionCard title="Team" icon={Users}>
      <div className="flex flex-col gap-2">
        {team.map((agentId) => (
          <TeamMemberRow key={agentId} agentId={agentId} isLead={agentId === project.lead} />
        ))}
      </div>
    </SectionCard>
  )
}
