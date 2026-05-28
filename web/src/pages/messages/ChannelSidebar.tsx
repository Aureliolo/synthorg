import { useMemo, useState } from 'react'
import { ChevronDown, ChevronRight, ListFilter, MessageSquare } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Drawer } from '@/components/ui/drawer'
import { Skeleton } from '@/components/ui/skeleton'
import { EmptyState } from '@/components/ui/empty-state'
import { ChannelListItem } from './ChannelListItem'
import { getChannelTypeLabel } from '@/utils/messages'
import type { Channel, ChannelType } from '@/api/types/messages'

const TYPE_ORDER: ChannelType[] = ['topic', 'direct', 'broadcast']

interface ChannelSidebarProps {
  channels: Channel[]
  activeChannel: string | null
  unreadCounts: Record<string, number>
  /** Channels we have direct evidence carry at least one message.
   *  Channels NOT in this set get demoted to the collapsed "Empty"
   *  group below the active list. */
  channelsWithMessages: ReadonlySet<string>
  onSelectChannel: (name: string) => void
  loading: boolean
}

/**
 * Returns true when a channel should be classified as "active": it has at
 * least one known message, has an unread badge, or is the currently-selected
 * channel. Anything else falls into the collapsed "Empty" group so a fresh
 * install's long list of pre-created topics doesn't bury the active threads.
 */
function isChannelActive(
  channel: Channel,
  activeChannel: string | null,
  channelsWithMessages: ReadonlySet<string>,
  unreadCounts: Record<string, number>,
): boolean {
  if (channel.name === activeChannel) return true
  if ((unreadCounts[channel.name] ?? 0) > 0) return true
  return channelsWithMessages.has(channel.name)
}

interface BucketedChannels {
  activeByType: Map<ChannelType, Channel[]>
  emptyByType: Map<ChannelType, Channel[]>
  emptyCount: number
}

function bucketChannels(
  channels: Channel[],
  activeChannel: string | null,
  channelsWithMessages: ReadonlySet<string>,
  unreadCounts: Record<string, number>,
): BucketedChannels {
  const activeMap = new Map<ChannelType, Channel[]>()
  const emptyMap = new Map<ChannelType, Channel[]>()
  let emptyTotal = 0
  for (const ch of channels) {
    const target = isChannelActive(ch, activeChannel, channelsWithMessages, unreadCounts)
      ? activeMap
      : emptyMap
    const bucket = target.get(ch.type)
    if (bucket) bucket.push(ch)
    else target.set(ch.type, [ch])
    if (target === emptyMap) emptyTotal++
  }
  return { activeByType: activeMap, emptyByType: emptyMap, emptyCount: emptyTotal }
}

interface ChannelListBodyProps extends ChannelSidebarProps {
  onAfterSelect?: () => void
}

function ChannelListBody(props: ChannelListBodyProps) {
  const {
    channels,
    activeChannel,
    unreadCounts,
    channelsWithMessages,
    onSelectChannel,
    loading,
    onAfterSelect,
  } = props
  const [emptyExpanded, setEmptyExpanded] = useState(false)

  // Bucket channels by type AND by activity. An "active" channel is the
  // currently-selected one, anything carrying unread messages, or anything
  // ``fetchChannelActivity`` found a recent message for; an "empty" channel
  // is everything else (typically the pre-created topics on a fresh install).
  const { activeByType, emptyByType, emptyCount } = useMemo(
    () => bucketChannels(channels, activeChannel, channelsWithMessages, unreadCounts),
    [channels, activeChannel, channelsWithMessages, unreadCounts],
  )

  const handleSelect = (name: string) => {
    onSelectChannel(name)
    onAfterSelect?.()
  }

  if (loading && channels.length === 0) return <ChannelListSkeleton />
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
      <ActiveChannelSections
        activeByType={activeByType}
        activeChannel={activeChannel}
        unreadCounts={unreadCounts}
        onSelect={handleSelect}
      />
      {emptyCount > 0 && (
        <EmptyChannelsSection
          expanded={emptyExpanded}
          onToggle={() => setEmptyExpanded((v) => !v)}
          emptyCount={emptyCount}
          emptyByType={emptyByType}
          activeChannel={activeChannel}
          unreadCounts={unreadCounts}
          onSelect={handleSelect}
        />
      )}
    </div>
  )
}

function ChannelListSkeleton() {
  return (
    <div className="flex flex-col gap-2">
      <div className="mb-1 px-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        Channels
      </div>
      {Array.from({ length: 6 }, (_, i) => (
        <Skeleton key={i} className="h-8 w-full rounded-md" />
      ))}
    </div>
  )
}

interface ChannelGroupSectionProps {
  type: ChannelType
  items: Channel[]
  activeChannel: string | null
  unreadCounts: Record<string, number>
  onSelect: (name: string) => void
}

function ChannelGroupSection({
  type,
  items,
  activeChannel,
  unreadCounts,
  onSelect,
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
            onSelect={onSelect}
          />
        ))}
      </div>
    </div>
  )
}

interface ActiveChannelSectionsProps {
  activeByType: Map<ChannelType, Channel[]>
  activeChannel: string | null
  unreadCounts: Record<string, number>
  onSelect: (name: string) => void
}

function ActiveChannelSections({
  activeByType,
  activeChannel,
  unreadCounts,
  onSelect,
}: ActiveChannelSectionsProps) {
  const rendered = TYPE_ORDER.flatMap((type) => {
    const items = activeByType.get(type)
    if (!items || items.length === 0) return []
    return [
      <ChannelGroupSection
        key={type}
        type={type}
        items={items}
        activeChannel={activeChannel}
        unreadCounts={unreadCounts}
        onSelect={onSelect}
      />,
    ]
  })
  if (rendered.length > 0) return <>{rendered}</>
  return (
    <div className="px-2 text-xs text-text-secondary">No channels with activity yet.</div>
  )
}

interface EmptyChannelsSectionProps {
  expanded: boolean
  onToggle: () => void
  emptyCount: number
  emptyByType: Map<ChannelType, Channel[]>
  activeChannel: string | null
  unreadCounts: Record<string, number>
  onSelect: (name: string) => void
}

function EmptyChannelsSection({
  expanded,
  onToggle,
  emptyCount,
  emptyByType,
  activeChannel,
  unreadCounts,
  onSelect,
}: EmptyChannelsSectionProps) {
  // Empty-channels section: collapsed by default so the user can see active
  // threads at a glance. Includes a chevron toggle and a count so they know
  // more channels exist below the fold.
  return (
    <div className="mt-1 border-t border-border pt-3">
      <button
        type="button"
        aria-expanded={expanded}
        onClick={onToggle}
        className="flex w-full items-center gap-1 px-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded"
      >
        {expanded ? (
          <ChevronDown className="size-3" aria-hidden="true" />
        ) : (
          <ChevronRight className="size-3" aria-hidden="true" />
        )}
        <span>Empty ({emptyCount})</span>
      </button>
      {expanded && (
        <div className="mt-2 flex flex-col gap-3">
          {TYPE_ORDER.map((type) => {
            const items = emptyByType.get(type)
            if (!items || items.length === 0) return null
            return (
              <ChannelGroupSection
                key={type}
                type={type}
                items={items}
                activeChannel={activeChannel}
                unreadCounts={unreadCounts}
                onSelect={onSelect}
              />
            )
          })}
        </div>
      )}
    </div>
  )
}

export function ChannelSidebar(props: ChannelSidebarProps) {
  const { channels, activeChannel, loading } = props
  const [drawerOpen, setDrawerOpen] = useState(false)

  // Active channel label drives the mobile trigger button so the operator
  // sees which channel they're currently viewing without opening the picker.
  const activeChannelLabel = useMemo(() => {
    if (!activeChannel) return 'Select channel'
    const match = channels.find((ch) => ch.name === activeChannel)
    return match?.name ?? activeChannel
  }, [activeChannel, channels])

  return (
    <>
      {/* Mobile (< lg): a hamburger button opens a Drawer with the same
          channel list. The Drawer closes on selection so the operator drops
          back into the message stream immediately. */}
      <div className="lg:hidden">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => setDrawerOpen(true)}
          aria-label={
            loading
              ? 'Loading channels'
              : `Pick channel (current: ${activeChannelLabel})`
          }
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
