import { useCallback } from 'react'
import { Bell } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useNotificationsStore } from '@/stores/notifications'
import { SIDEBAR_BUTTON_CLASS } from './sidebar-button'

export interface NotificationBellProps {
  collapsed: boolean
}

function _renderBadge(count: number) {
  return (
    <span
      className="absolute -right-1.5 -top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-danger px-1 text-micro font-semibold text-white"
      aria-hidden="true"
    >
      {count > 99 ? '99+' : String(count)}
    </span>
  )
}

export function NotificationBell({ collapsed }: NotificationBellProps) {
  const unreadCount = useNotificationsStore((s) => s.unreadCount)
  const ariaLabel = unreadCount > 0
    ? `Notifications (${String(unreadCount)} unread)`
    : 'Notifications'
  // Memoised so child <button> doesn't take a fresh prop identity on
  // every parent render. Mirrors the pattern used in
  // `AppLayout.openNotificationDrawer`.
  const openNotificationDrawer = useCallback(() => {
    window.dispatchEvent(new CustomEvent('open-notification-drawer'))
  }, [])
  return (
    <button
      type="button"
      title="Notifications (Shift+N)"
      aria-label={ariaLabel}
      className={SIDEBAR_BUTTON_CLASS}
      onClick={openNotificationDrawer}
    >
      <span className="relative">
        <Bell
          className={cn('size-5 shrink-0', collapsed && 'mx-auto')}
          aria-hidden="true"
        />
        {unreadCount > 0 && _renderBadge(unreadCount)}
      </span>
      {!collapsed && (
        <span className="flex flex-1 items-center justify-between gap-2">
          <span>Notifications</span>
          {unreadCount > 0 && (
            <span className="text-xs text-muted-foreground" aria-live="polite">
              {String(unreadCount)}
            </span>
          )}
        </span>
      )}
    </button>
  )
}
