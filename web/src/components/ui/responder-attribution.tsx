import { cn } from '@/lib/utils'

import { Avatar } from './avatar'

export interface ResponderAttributionProps {
  /** Display name of the responding role agent. */
  name: string
  /** Role of the responding agent (e.g. "CFO"). */
  role: string
  /** Optional concern topic that selected the role (e.g. "budget"). */
  topic?: string | null
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
  className,
}: ResponderAttributionProps) {
  return (
    <div
      className={cn(
        'mt-1 flex items-center gap-1.5 text-xs text-text-secondary',
        className,
      )}
    >
      <Avatar name={name} size="sm" />
      <span className="font-medium text-foreground">{name}</span>
      <span className="text-muted-foreground">·</span>
      <span>{role}</span>
      {topic && (
        <span className="text-muted-foreground">
          · routed by <span className="font-mono">{topic}</span>
        </span>
      )}
    </div>
  )
}
