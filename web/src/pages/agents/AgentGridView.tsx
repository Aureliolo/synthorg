import { memo } from 'react'
import { Link } from 'react-router'
import { Users } from 'lucide-react'
import { AgentCard } from '@/components/ui/agent-card'
import { EmptyState } from '@/components/ui/empty-state'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { toRuntimeStatus } from '@/utils/agents'
import { formatRelativeTime } from '@/utils/format'
import { ROUTES } from '@/router/routes'
import { cn } from '@/lib/utils'
import type { AgentConfig } from '@/api/types/agents'

interface AgentGridViewProps {
  agents: readonly AgentConfig[]
  className?: string
}

function AgentGridItemComponent({ agent }: { agent: AgentConfig }) {
  return (
    <StaggerItem>
      <Link
        to={ROUTES.AGENT_DETAIL.replace(':agentId', encodeURIComponent(agent.id ?? agent.name))}
        className="block focus:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 rounded-lg"
      >
        <AgentCard
          name={agent.name}
          role={agent.role}
          department={agent.department}
          status={toRuntimeStatus(agent.status ?? 'active')}
          timestamp={agent.hiring_date ? formatRelativeTime(agent.hiring_date) : undefined}
        />
      </Link>
    </StaggerItem>
  )
}

const AgentGridItem = memo(AgentGridItemComponent)

export function AgentGridView({ agents, className }: AgentGridViewProps) {
  if (agents.length === 0) {
    return (
      <EmptyState
        icon={Users}
        title="No agents found"
        description="Try adjusting your filters or search query."
      />
    )
  }

  return (
    <StaggerGroup
      className={cn(
        'grid grid-cols-4 gap-grid-gap max-[1279px]:grid-cols-3 max-[1023px]:grid-cols-2',
        className,
      )}
    >
      {agents.map((agent) => (
        <AgentGridItem key={agent.id ?? agent.name} agent={agent} />
      ))}
    </StaggerGroup>
  )
}
