import { cn } from '@/lib/utils'
import type { SettingSource } from '@/api/types/settings'

const SOURCE_STYLES: Record<SettingSource, string> = {
  db: 'bg-accent/10 text-accent',
  env: 'bg-warning/10 text-warning',
  default: 'bg-border text-text-muted',
}

const SOURCE_LABELS: Record<SettingSource, string | null> = {
  db: 'Modified',
  env: 'ENV',
  default: null,
}

export interface SourceBadgeProps {
  source: SettingSource
  className?: string
}

export function SourceBadge({ source, className }: SourceBadgeProps) {
  const label = SOURCE_LABELS[source]
  if (label == null) return null

  return (
    <span
      className={cn(
        'inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider',
        SOURCE_STYLES[source],
        className,
      )}
    >
      {label}
    </span>
  )
}
