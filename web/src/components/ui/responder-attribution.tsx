import { cn } from '@/lib/utils'

import { Avatar } from './avatar'
import { Skeleton } from './skeleton'

export interface ResponderAttributionProps {
  /** Display name of the responding role agent. */
  name: string
  /** Role of the responding agent (e.g. "CFO"). */
  role: string
  /** Optional concern topic that selected the role (e.g. "budget"). */
  topic?: string | null
  /** When true, render a placeholder while the routing decision resolves. */
  loading?: boolean
  className?: string
}

/**
 * Inline attribution for a concern-routed conversational response.
 *
 * Renders the responding role agent's avatar, name, and role so a human
 * can see which agent answered a routed turn. Shared by the
 * conversational propose surface and the multi-agent group chat.
 */
export function ResponderAttribution({
  name,
  role,
  topic,
  loading = false,
  className,
}: ResponderAttributionProps) {
  if (loading) {
    return (
      <div
        className={cn('mt-1 flex items-center gap-1.5', className)}
        role="status"
        aria-label="Resolving responder"
      >
        <Skeleton className="size-5 rounded-full" />
        <Skeleton className="h-3 w-20" />
        <Skeleton className="h-3 w-10" />
      </div>
    )
  }
  return (
    <div
      className={cn(
        'mt-1 flex items-center gap-1.5 text-xs text-text-secondary',
        className,
      )}
    >
      <span aria-hidden="true">
        <Avatar name={name} size="sm" />
      </span>
      <span className="font-medium text-foreground">{name}</span>
      <span aria-hidden="true" className="text-muted-foreground">
        ·
      </span>
      <span>{role}</span>
      {topic && (
        <span className="text-muted-foreground">
          <span aria-hidden="true">· </span>routed by{' '}
          <span className="font-mono">{topic}</span>
        </span>
      )}
    </div>
  )
}
