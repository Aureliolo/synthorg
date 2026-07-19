import type { ConnectionType } from '@/api/types/integrations'
import { cn } from '@/lib/utils'
import { useConnectionsStore } from '@/stores/connections'
import { connectionTypeLabel } from './connection-fields'

interface TypeBadgeProps {
  type: ConnectionType
  className?: string
}

export function TypeBadge({ type, className }: TypeBadgeProps) {
  const connectionTypes = useConnectionsStore((s) => s.connectionTypes)
  const label = connectionTypeLabel(type, connectionTypes)
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-md border border-border bg-surface',
        'px-2 py-0.5 font-mono text-[11px] text-text-secondary',
        className,
      )}
    >
      {label}
    </span>
  )
}
