import { Link } from 'react-router'
import { Users } from 'lucide-react'
import { SectionCard } from '@/components/ui/section-card'
import { Avatar } from '@/components/ui/avatar'
import { EmptyState } from '@/components/ui/empty-state'
import { ROUTES } from '@/router/routes'
import { UNKNOWN_AGENT_NAME } from '@/utils/agents'
import type { ContributorRef } from '@/api/types/projects'

interface ProjectTeamSectionProps {
  /** Agents derived from the tasks that ran on this initiative. */
  contributors: readonly ContributorRef[]
  /** The accountable lead's id, marked out among the contributors. */
  lead: string | null
}

// The id is the link target; the name is the only half rendered. An agent the
// roster no longer covers still links, so the operator can reach whatever
// record survives them.
function ContributorRow({
  contributor,
  isLead,
}: {
  contributor: ContributorRef
  isLead: boolean
}) {
  const label = contributor.name ?? UNKNOWN_AGENT_NAME
  return (
    <Link
      to={ROUTES.AGENT_DETAIL.replace(':agentId', encodeURIComponent(contributor.id))}
      className="flex items-center gap-2 rounded-md px-2 py-1.5 transition-colors hover:bg-accent/5"
    >
      <Avatar name={label} />
      <span className="text-sm text-foreground">{label}</span>
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
          description="Agents appear here once they start work on this initiative."
        />
      </SectionCard>
    )
  }

  return (
    <SectionCard title="Contributors" icon={Users}>
      <div className="flex flex-col gap-2">
        {contributors.map((contributor) => (
          <ContributorRow
            key={contributor.id}
            contributor={contributor}
            isLead={contributor.id === lead}
          />
        ))}
      </div>
    </SectionCard>
  )
}
