import { cn } from '@/lib/utils'
import { getMessageTypeLabel } from '@/utils/messages'
import type { MessageType } from '@/api/types/messages'

interface MessageTypeBadgeProps {
  type: MessageType
  className?: string
}

export function MessageTypeBadge({ type, className }: MessageTypeBadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded border border-border bg-surface px-1.5 py-0.5',
        'font-mono text-[10px] text-text-secondary',
        className,
      )}
    >
      {getMessageTypeLabel(type)}
    </span>
  )
}
