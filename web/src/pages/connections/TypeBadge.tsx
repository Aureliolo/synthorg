import type { ConnectionType } from '@/api/types/integrations'
import { cn } from '@/lib/utils'
import { connectionTypeLabel } from './connection-fields'
import { useConnectionTypes } from './useConnectionTypes'

interface TypeBadgeProps {
  type: ConnectionType
  className?: string
}

export function TypeBadge({ type, className }: TypeBadgeProps) {
  const label = connectionTypeLabel(type, useConnectionTypes())
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-md border border-border bg-surface',
        'px-2 py-0.5 font-mono text-xs text-text-secondary',
        className,
      )}
    >
      {label}
    </span>
  )
}
