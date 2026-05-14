import { Plug } from 'lucide-react'
import type { Connection, HealthReport } from '@/api/types/integrations'
import { EmptyState } from '@/components/ui/empty-state'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { ConnectionCard } from './ConnectionCard'

export interface ConnectionGridViewProps {
  connections: readonly Connection[]
  healthMap: Record<string, HealthReport>
  checkingHealth: readonly string[]
  onRunHealthCheck: (name: string) => void
  onEdit: (connection: Connection) => void
  onDelete: (connection: Connection) => void
  onCreate?: () => void
}

export function ConnectionGridView({
  connections,
  healthMap,
  checkingHealth,
  onRunHealthCheck,
  onEdit,
  onDelete,
  onCreate,
}: ConnectionGridViewProps) {
  if (connections.length === 0) {
    return (
      <EmptyState
        icon={Plug}
        title="No connections configured"
        description="Connect SynthOrg to an external service: code hosting, messaging, email, databases, and more."
        action={onCreate ? { label: 'New Connection', onClick: onCreate } : undefined}
      />
    )
  }

  return (
    <StaggerGroup className="grid grid-cols-3 gap-grid-gap max-[1023px]:grid-cols-2 max-[767px]:grid-cols-1">
      {connections.map((connection) => (
        <StaggerItem key={connection.name}>
          <ConnectionCard
            connection={connection}
            report={healthMap[connection.name] ?? null}
            checking={checkingHealth.includes(connection.name)}
            onRunHealthCheck={() => onRunHealthCheck(connection.name)}
            onEdit={() => onEdit(connection)}
            onDelete={() => onDelete(connection)}
          />
        </StaggerItem>
      ))}
    </StaggerGroup>
  )
}
