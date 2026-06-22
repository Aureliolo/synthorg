import { memo } from 'react'
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

interface ConnectionGridRowProps {
  connection: Connection
  report: HealthReport | null
  checking: boolean
  onRunHealthCheck: (name: string) => void
  onEdit: (connection: Connection) => void
  onDelete: (connection: Connection) => void
}

// Memoised row: the per-connection click closures are created from stable
// parent callbacks, so an unaffected card does not re-render when sibling
// state (a sibling's health check, the filter input) changes.
const ConnectionGridRow = memo(function ConnectionGridRow({
  connection,
  report,
  checking,
  onRunHealthCheck,
  onEdit,
  onDelete,
}: ConnectionGridRowProps) {
  return (
    <ConnectionCard
      connection={connection}
      report={report}
      checking={checking}
      onRunHealthCheck={() => onRunHealthCheck(connection.name)}
      onEdit={() => onEdit(connection)}
      onDelete={() => onDelete(connection)}
    />
  )
})

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
          <ConnectionGridRow
            connection={connection}
            report={healthMap[connection.name] ?? null}
            checking={checkingHealth.includes(connection.name)}
            onRunHealthCheck={onRunHealthCheck}
            onEdit={onEdit}
            onDelete={onDelete}
          />
        </StaggerItem>
      ))}
    </StaggerGroup>
  )
}
