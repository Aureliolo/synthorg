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
      className="absolute -right-1.5 -top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-danger px-1 text-[10px] font-semibold text-white"
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
  return (
    <button
      type="button"
      title="Notifications (Shift+N)"
      aria-label={ariaLabel}
      className={SIDEBAR_BUTTON_CLASS}
      onClick={() => window.dispatchEvent(new CustomEvent('open-notification-drawer'))}
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
