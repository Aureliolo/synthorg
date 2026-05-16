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
  /**
   * Optional selection set keyed on the agent's stable id (or name
   * fallback). When provided alongside ``onToggleSelect``, each card
   * renders a checkbox overlay; otherwise the grid stays
   * selection-unaware so unrelated callers (detail-page sidebars,
   * dashboard widgets) keep their existing layout.
   */
  selectedIds?: ReadonlySet<string>
  onToggleSelect?: (id: string) => void
}

function agentKey(agent: AgentConfig): string {
  return agent.id ?? agent.name
}

interface AgentGridItemProps {
  agent: AgentConfig
  selected?: boolean
  onToggleSelect?: (id: string) => void
}

function AgentGridItemComponent({ agent, selected, onToggleSelect }: AgentGridItemProps) {
  const id = agentKey(agent)
  return (
    <StaggerItem>
      <div className="relative">
        {onToggleSelect && (
          <label
            className="absolute left-2 top-2 z-10 flex h-6 w-6 cursor-pointer items-center justify-center rounded border border-border bg-card shadow-sm"
            onClick={(e) => e.stopPropagation()}
          >
            <input
              type="checkbox"
              checked={selected ?? false}
              onChange={() => onToggleSelect(id)}
              aria-label={`Select agent ${agent.name}`}
              className="h-4 w-4 cursor-pointer accent-accent"
            />
          </label>
        )}
        <Link
          to={ROUTES.AGENT_DETAIL.replace(':agentId', encodeURIComponent(id))}
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
      </div>
    </StaggerItem>
  )
}

const AgentGridItem = memo(AgentGridItemComponent)

export function AgentGridView({ agents, className, selectedIds, onToggleSelect }: AgentGridViewProps) {
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
      {agents.map((agent) => {
        const id = agentKey(agent)
        return (
          <AgentGridItem
            key={id}
            agent={agent}
            selected={selectedIds?.has(id)}
            onToggleSelect={onToggleSelect}
          />
        )
      })}
    </StaggerGroup>
  )
}
