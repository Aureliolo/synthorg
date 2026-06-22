import type { ReactNode } from 'react'
import { HeartPulse } from 'lucide-react'

import { SectionCard } from '@/components/ui/section-card'
import { StatusBadge } from '@/components/ui/status-badge'
import type { AgentHealthResponse } from '@/api/types'
import { toRuntimeStatus } from '@/utils/agents'
import { formatDateTime, formatRelativeTime } from '@/utils/format'

export interface AgentHealthCardProps {
  health: AgentHealthResponse | null
}

/**
 * Compact health summary for the agent detail page: lifecycle status,
 * trust level, and last-active timestamp sourced from
 * ``GET /agents/{id}/health``. Renders nothing until health has loaded.
 */
export function AgentHealthCard({ health }: AgentHealthCardProps) {
  if (!health) return null
  const trustLevel = health.trust ? health.trust.level : '--'
  const lastActiveAt = health.last_active_at
  return (
    <SectionCard title="Health" icon={HeartPulse}>
      <dl className="grid grid-cols-3 gap-grid-gap max-[1023px]:grid-cols-1">
        <HealthField label="Lifecycle">
          <StatusBadge
            status={toRuntimeStatus(health.lifecycle_status)}
            label
          />
        </HealthField>
        <HealthField label="Trust level">
          <span className="text-sm text-foreground">{trustLevel}</span>
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
