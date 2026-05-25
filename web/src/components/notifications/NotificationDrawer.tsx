import { useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Drawer } from '@/components/ui/drawer'
import { LiveRegion } from '@/components/ui/live-region'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { useNotificationsStore } from '@/stores/notifications'
import type { NotificationFilterGroup } from '@/types/notifications'
import { CATEGORY_CONFIGS } from '@/types/notifications'

import { NotificationEmptyState } from './NotificationEmptyState'
import { NotificationFilterBar } from './NotificationFilterBar'
import { NotificationItemCard } from './NotificationItemCard'

interface NotificationDrawerProps {
  readonly open: boolean
  readonly onClose: () => void
}

function NotificationSummary({
  unreadCount,
  onMarkAllRead,
}: {
  unreadCount: number
  onMarkAllRead: () => void
}) {
  return (
    <div className="flex items-center justify-between">
      <LiveRegion>
        <span className="text-xs text-muted-foreground">
          {unreadCount > 0 ? `${unreadCount} unread` : 'All read'}
        </span>
      </LiveRegion>
      {unreadCount > 0 && (
        <Button variant="ghost" size="sm" onClick={onMarkAllRead} className="text-xs">
          Mark all read
        </Button>
      )}
    </div>
  )
}

export function NotificationDrawer({ open, onClose }: NotificationDrawerProps) {
  const items = useNotificationsStore((s) => s.items)
  const markRead = useNotificationsStore((s) => s.markRead)
  const markReadBatch = useNotificationsStore((s) => s.markReadBatch)
  const dismiss = useNotificationsStore((s) => s.dismiss)
  const dismissBatch = useNotificationsStore((s) => s.dismissBatch)

  const [filter, setFilter] = useState<NotificationFilterGroup>('all')

  const filteredItems = useMemo(() => {
    if (filter === 'all') return items
    return items.filter((item) => CATEGORY_CONFIGS[item.category].group === filter)
  }, [items, filter])

  const filteredUnreadCount = useMemo(
    () => filteredItems.filter((item) => !item.read).length,
    [filteredItems],
  )

  const handleMarkAllRead = (): void => {
    const ids = filteredItems.filter((item) => !item.read).map((item) => item.id)
    if (ids.length > 0) markReadBatch(ids)
  }

  const handleClearAll = (): void => {
    const ids = filteredItems.map((item) => item.id)
    if (ids.length > 0) dismissBatch(ids)
  }

  return (
    <Drawer open={open} onClose={onClose} title="Notifications" side="right">
      <div className="flex h-full flex-col gap-3 p-card">
        <NotificationFilterBar value={filter} onChange={setFilter} />
        <NotificationSummary
          unreadCount={filteredUnreadCount}
          onMarkAllRead={handleMarkAllRead}
        />
        <div className="flex-1 overflow-y-auto">
          {filteredItems.length === 0 ? (
            <NotificationEmptyState filter={filter} />
          ) : (
            <div role="list">
              <StaggerGroup className="flex flex-col gap-1">
                {filteredItems.map((item) => (
                  <StaggerItem key={item.id}>
                    <NotificationItemCard
                      item={item}
                      onMarkRead={markRead}
                      onDismiss={dismiss}
                    />
                  </StaggerItem>
                ))}
              </StaggerGroup>
            </div>
          )}
        </div>
        {filteredItems.length > 0 && (
          <div className="border-t border-border pt-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={handleClearAll}
              className="w-full text-xs text-muted-foreground"
            >
              Clear all
            </Button>
          </div>
        )}
      </div>
    </Drawer>
  )
}
