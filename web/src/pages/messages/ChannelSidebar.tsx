import { useMemo } from 'react'
import { MessageSquare } from 'lucide-react'
import { Skeleton } from '@/components/ui/skeleton'
import { EmptyState } from '@/components/ui/empty-state'
import { ChannelListItem } from './ChannelListItem'
import { getChannelTypeLabel } from '@/utils/messages'
import type { Channel, ChannelType } from '@/api/types/messages'

const TYPE_ORDER: ChannelType[] = [
  'topic',
  'direct',
  'broadcast',
]

interface ChannelGroupSectionProps {
  type: ChannelType
  items: Channel[]
  activeChannel: string | null
  unreadCounts: Record<string, number>
  onSelectChannel: (name: string) => void
}

function ChannelGroupSection({
  type,
  items,
  activeChannel,
  unreadCounts,
  onSelectChannel,
}: ChannelGroupSectionProps) {
  return (
    <div>
      <div className="mb-1 px-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        {getChannelTypeLabel(type)}
      </div>
      <div className="flex flex-col gap-0.5">
        {items.map((ch) => (
          <ChannelListItem
            key={ch.name}
            channel={ch}
            active={ch.name === activeChannel}
            unreadCount={unreadCounts[ch.name] ?? 0}
            onClick={() => onSelectChannel(ch.name)}
          />
        ))}
      </div>
    </div>
  )
}

interface ChannelSidebarProps {
  channels: Channel[]
  activeChannel: string | null
  unreadCounts: Record<string, number>
  onSelectChannel: (name: string) => void
  loading: boolean
}

export function ChannelSidebar({
  channels,
  activeChannel,
  unreadCounts,
  onSelectChannel,
  loading,
}: ChannelSidebarProps) {
  const grouped = useMemo(() => {
    const map = new Map<ChannelType, Channel[]>()
    for (const ch of channels) {
      const bucket = map.get(ch.type)
      if (bucket) {
        bucket.push(ch)
      } else {
        map.set(ch.type, [ch])
      }
    }
    return map
  }, [channels])

  if (loading && channels.length === 0) {
    return (
      <nav aria-label="Channels" className="hidden w-56 shrink-0 flex-col gap-2 border-r border-border pr-4 lg:flex">
        <div className="mb-1 px-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">Channels</div>
        {Array.from({ length: 6 }, (_, i) => (
          <Skeleton key={i} className="h-8 w-full rounded-md" />
        ))}
      </nav>
    )
  }

  if (channels.length === 0) {
    return (
      <nav aria-label="Channels" className="hidden w-56 shrink-0 flex-col border-r border-border pr-4 lg:flex">
        <EmptyState
          icon={MessageSquare}
          title="No channels"
          description="No communication channels have been created yet."
        />
      </nav>
    )
  }

  return (
    <nav aria-label="Channels" className="hidden w-56 shrink-0 flex-col gap-3 overflow-y-auto border-r border-border pr-4 lg:flex">
      {TYPE_ORDER.map((type) => {
        const items = grouped.get(type)
        if (!items || items.length === 0) return null
        return (
          <ChannelGroupSection
            key={type}
            type={type}
            items={items}
            activeChannel={activeChannel}
            unreadCounts={unreadCounts}
            onSelectChannel={onSelectChannel}
          />
        )
      })}
    </nav>
  )
}
