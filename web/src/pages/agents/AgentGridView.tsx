import { memo } from 'react'
import { Link } from 'react-router'
import { Users } from 'lucide-react'
import { AgentCard } from '@/components/ui/agent-card'
import { Checkbox } from '@/components/ui/checkbox'
import { EmptyState } from '@/components/ui/empty-state'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { toRuntimeStatus } from '@/utils/agents'
import { formatRelativeTime } from '@/utils/format'
import { ROUTES } from '@/router/routes'
import { cn } from '@/lib/utils'
import type { AgentConfig } from '@/api/types/agents'

interface AgentGridViewProps {
  agents: readonly AgentConfig[]
  className?: string | undefined
  /**
   * Optional selection set keyed on the agent's stable UUID id. When
   * provided alongside ``onToggleSelect``, each card
   * renders a checkbox overlay; otherwise the grid stays
   * selection-unaware so unrelated callers (detail-page sidebars,
   * dashboard widgets) keep their existing layout.
   */
  selectedIds?: ReadonlySet<string> | undefined
  onToggleSelect?: ((id: string) => void) | undefined
}

function agentKey(agent: AgentConfig): string {
  return agent.id
}

/** Best-effort model identifier from the agent's raw model config dict. */
function agentModelId(agent: AgentConfig): string | undefined {
  const id = agent.model['model_id']
  return typeof id === 'string' && id ? id : undefined
}

interface AgentGridItemProps {
  agent: AgentConfig
  selected?: boolean | undefined
  onToggleSelect?: ((id: string) => void) | undefined
}

function AgentGridItemComponent({ agent, selected, onToggleSelect }: AgentGridItemProps) {
  const id = agentKey(agent)
  return (
    <StaggerItem>
      <div className="relative">
        {onToggleSelect && (
          <div
            className="absolute right-2 top-2 z-10"
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => e.stopPropagation()}
          >
            <Checkbox
              checked={selected ?? false}
              onCheckedChange={() => onToggleSelect(id)}
              aria-label={`Select agent ${agent.name}`}
            />
          </div>
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
            model={agentModelId(agent)}
            tier={agent.tier}
            timestamp={agent.hiring_date ? formatRelativeTime(agent.hiring_date) : undefined}
            timestampIso={agent.hiring_date ?? undefined}
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
