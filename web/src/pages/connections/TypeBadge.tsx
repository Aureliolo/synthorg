import type { ConnectionType } from '@/api/types/integrations'
import { cn } from '@/lib/utils'
import { CONNECTION_TYPE_FIELDS } from './connection-type-fields'

interface TypeBadgeProps {
  type: ConnectionType
  className?: string
}

export function TypeBadge({ type, className }: TypeBadgeProps) {
  const spec = CONNECTION_TYPE_FIELDS[type]
  const label = spec.label
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
