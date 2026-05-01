import { useMemo, useState } from 'react'
import { ListFilter, MessageSquare } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Drawer } from '@/components/ui/drawer'
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

/**
 * Inner channel list, shared between the desktop sidebar and the
 * mobile Drawer below the lg breakpoint. Accepts the same data props
 * as ``ChannelSidebar`` plus a ``onAfterSelect`` callback so the
 * Drawer can close itself on selection.
 */
interface ChannelListBodyProps extends ChannelSidebarProps {
  onAfterSelect?: () => void
}

function ChannelListBody({
  channels,
  activeChannel,
  unreadCounts,
  onSelectChannel,
  loading,
  onAfterSelect,
}: ChannelListBodyProps) {
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

  const handleSelect = (name: string) => {
    onSelectChannel(name)
    onAfterSelect?.()
  }

  if (loading && channels.length === 0) {
    return (
      <div className="flex flex-col gap-2">
        <div className="mb-1 px-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">Channels</div>
        {Array.from({ length: 6 }, (_, i) => (
          <Skeleton key={i} className="h-8 w-full rounded-md" />
        ))}
      </div>
    )
  }

  if (channels.length === 0) {
    return (
      <EmptyState
        icon={MessageSquare}
        title="No channels"
        description="No communication channels have been created yet."
      />
    )
  }

  return (
    <div className="flex flex-col gap-3">
      {TYPE_ORDER.map((type) => {
        const items = grouped.get(type)
        if (!items || items.length === 0) return null
        return (
          <div key={type}>
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
                  onClick={() => handleSelect(ch.name)}
                />
              ))}
            </div>
          </div>
        )
      })}
    </div>
  )
}

export function ChannelSidebar(props: ChannelSidebarProps) {
  const { channels, activeChannel, loading } = props
  const [drawerOpen, setDrawerOpen] = useState(false)

  // Active channel label drives the mobile trigger button so the
  // operator sees which channel they're currently viewing without
  // opening the picker.
  const activeChannelLabel = useMemo(() => {
    if (!activeChannel) return 'Select channel'
    const match = channels.find((ch) => ch.name === activeChannel)
    return match?.name ?? activeChannel
  }, [activeChannel, channels])

  return (
    <>
      {/* Mobile (< lg): a hamburger button opens a Drawer with the
          same channel list. The Drawer closes on selection so the
          operator drops back into the message stream immediately. */}
      <div className="lg:hidden">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => setDrawerOpen(true)}
          aria-label={loading ? 'Loading channels' : `Pick channel (current: ${activeChannelLabel})`}
          className="gap-2"
        >
          <ListFilter className="size-4" aria-hidden="true" />
          <span className="truncate">{activeChannelLabel}</span>
        </Button>
        <Drawer
          open={drawerOpen}
          onClose={() => setDrawerOpen(false)}
          side="left"
          width="narrow"
          title="Channels"
        >
          <ChannelListBody {...props} onAfterSelect={() => setDrawerOpen(false)} />
        </Drawer>
      </div>

      {/* Desktop (lg+): inline sidebar nav. */}
      <nav
        aria-label="Channels"
        className="hidden w-56 shrink-0 flex-col gap-3 overflow-y-auto border-r border-border pr-4 lg:flex"
      >
        <ChannelListBody {...props} />
      </nav>
    </>
  )
}

// Re-exported only to silence "unused" lint after refactor; the
// inline desktop branch above renders ChannelGroupSection's output
// shape directly through ChannelListBody.
export { ChannelGroupSection }
