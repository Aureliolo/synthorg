import { Link } from 'react-router'
import { Users } from 'lucide-react'
import { SectionCard } from '@/components/ui/section-card'
import { Avatar } from '@/components/ui/avatar'
import { EmptyState } from '@/components/ui/empty-state'
import { ROUTES } from '@/router/routes'

interface ProjectTeamSectionProps {
  /** Agent ids derived from the tasks that ran on this initiative. */
  contributors: readonly string[]
  /** The accountable lead, marked out among the contributors. */
  lead: string | null
}

function ContributorRow({ agentId, isLead }: { agentId: string; isLead: boolean }) {
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

export function ProjectTeamSection({ contributors, lead }: ProjectTeamSectionProps) {
  if (contributors.length === 0) {
    return (
      <SectionCard title="Contributors" icon={Users}>
        <EmptyState
          icon={Users}
          title="No contributors yet"
          description="Agents appear here once work is assigned on this initiative."
        />
      </SectionCard>
    )
  }

  return (
    <SectionCard title="Contributors" icon={Users}>
      <div className="flex flex-col gap-2">
        {contributors.map((agentId) => (
          <ContributorRow key={agentId} agentId={agentId} isLead={agentId === lead} />
        ))}
      </div>
    </SectionCard>
  )
}
