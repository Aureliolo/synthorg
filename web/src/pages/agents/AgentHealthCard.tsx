import type { ReactNode } from 'react'
import { HeartPulse, PlugZap } from 'lucide-react'

import { SectionCard } from '@/components/ui/section-card'
import { StatusBadge } from '@/components/ui/status-badge'
import { StatusPill } from '@/components/ui/status-pill'
import type { AgentHealthResponse } from '@/api/types/agents'
import { toRuntimeStatus } from '@/utils/agents'
import { formatDateTime, formatRelativeTime } from '@/utils/format'

export interface AgentHealthCardProps {
  health: AgentHealthResponse | null
}

/**
 * Compact health summary for the agent detail page: lifecycle status,
 * whether the agent's bound model can currently serve, and the last-active
 * timestamp, sourced from ``GET /agents/{id}/health``. Renders nothing until
 * health has loaded.
 *
 * Availability sits beside lifecycle because they answer different
 * questions: an ACTIVE agent whose model has stopped serving is out, and a
 * card showing only "Active" would leave an operator hunting for why nothing
 * is being routed to it.
 */
export function AgentHealthCard({ health }: AgentHealthCardProps) {
  if (!health) return null
  const lastActiveAt = health.last_active_at
  const unavailable = health.unavailable
  return (
    <SectionCard title="Health" icon={HeartPulse}>
      <dl className="grid grid-cols-2 gap-grid-gap max-[1023px]:grid-cols-1">
        <HealthField label="Lifecycle">
          <StatusBadge
            status={toRuntimeStatus(health.lifecycle_status)}
            label
          />
        </HealthField>
        <HealthField label="Availability">
          {unavailable !== null ? (
            <div className="space-y-1">
              <StatusPill
                tone={unavailable.needs_operator ? 'danger' : 'warning'}
                icon={PlugZap}
              >
                {unavailable.needs_operator ? 'Blocked' : 'Model down'}
              </StatusPill>
              <p className="text-xs text-text-secondary">{unavailable.reason}</p>
            </div>
          ) : (
            <StatusPill tone="success">Taking work</StatusPill>
          )}
        </HealthField>
        <HealthField label="Last active">
          {lastActiveAt !== null ? (
            <time
              dateTime={lastActiveAt}
              title={formatDateTime(lastActiveAt)}
              className="text-sm text-foreground"
            >
              {formatRelativeTime(lastActiveAt)}
            </time>
          ) : (
            <span className="text-sm text-foreground">--</span>
          )}
        </HealthField>
      </dl>
    </SectionCard>
  )
}

function HealthField({
  label,
  children,
}: {
  label: string
  children: ReactNode
}) {
  return (
    <div className="space-y-1">
      <dt className="text-xs font-semibold uppercase tracking-wider text-text-muted">
        {label}
      </dt>
      <dd>{children}</dd>
    </div>
  )
}
