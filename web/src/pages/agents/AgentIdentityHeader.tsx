import { Avatar } from '@/components/ui/avatar'
import { StatusBadge } from '@/components/ui/status-badge'
import { StatPill } from '@/components/ui/stat-pill'
import { toRuntimeStatus } from '@/utils/agents'
import { formatLabel, formatDateTime } from '@/utils/format'
import { cn } from '@/lib/utils'
import type { AgentConfig } from '@/api/types/agents'

interface AgentIdentityHeaderProps {
  agent: AgentConfig
  className?: string
}

export function AgentIdentityHeader({ agent, className }: AgentIdentityHeaderProps) {
  const runtimeStatus = toRuntimeStatus(agent.status ?? 'active')

  return (
    <div className={cn('flex items-start gap-4', className)}>
      <Avatar name={agent.name} size="lg" />

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-semibold text-foreground truncate">{agent.name}</h1>
          <StatusBadge
            status={runtimeStatus}
            label
            pulse={runtimeStatus === 'active'}
          />
        </div>

        <p className="text-sm text-secondary-foreground">{agent.role}</p>

        <div className="mt-2 flex flex-wrap items-center gap-2">
          <StatPill label="DEPT" value={formatLabel(agent.department)} />
          <StatPill label="LEVEL" value={formatLabel(agent.level)} />
          {agent.autonomy_level && (
            <StatPill label="AUTONOMY" value={formatLabel(agent.autonomy_level)} />
          )}
          {agent.hiring_date && (
            <time
              dateTime={agent.hiring_date}
              className="text-micro font-mono text-muted-foreground"
            >
              Hired {formatDateTime(agent.hiring_date)}
            </time>
          )}
        </div>
      </div>
    </div>
  )
}
