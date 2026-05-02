import { memo } from 'react'
import { Hash, Megaphone, Users } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { Channel } from '@/api/types/messages'

const CHANNEL_ICONS = {
  topic: Hash,
  direct: Users,
  broadcast: Megaphone,
} as const

interface ChannelListItemProps {
  channel: Channel
  active: boolean
  unreadCount: number
  onClick: () => void
}

function ChannelListItemImpl({
  channel,
  active,
  unreadCount,
  onClick,
}: ChannelListItemProps) {
  const Icon = CHANNEL_ICONS[channel.type]

  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? 'page' : undefined}
      className={cn(
        'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors',
        'hover:bg-card-hover',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
        active ? 'bg-card-hover text-foreground' : 'text-secondary',
      )}
    >
      <Icon className="size-3.5 shrink-0" aria-hidden="true" />
      <span className="min-w-0 flex-1 truncate font-mono text-xs">{channel.name}</span>
      {unreadCount > 0 && (
        <span
          aria-label={`${unreadCount} unread`}
          className="shrink-0 rounded-full bg-accent/15 px-1.5 font-mono text-[10px] text-accent"
        >
          {unreadCount}
        </span>
      )}
    </button>
  )
}

/**
 * Memoised so re-renders of the surrounding ChannelListBody (driven by
 * activeChannel + unreadCounts changes) don't ripple through every
 * row when only one row's `active` / `unreadCount` actually changed.
 */
export const ChannelListItem = memo(ChannelListItemImpl)
