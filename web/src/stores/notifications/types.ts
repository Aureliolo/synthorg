import type { StoreApi } from 'zustand'
import type {
  NotificationCategory,
  NotificationItem,
  NotificationPreferences,
  NotificationRoute,
  NotificationSeverity,
} from '@/types/notifications'
import type { WsEvent } from '@/api/types/websocket'

export interface EnqueueParams {
  readonly category: NotificationCategory
  readonly title: string
  readonly description?: string | undefined
  readonly href?: string | undefined
  readonly entityId?: string | undefined
  readonly severity?: NotificationSeverity | undefined
  /**
   * Optional inline action label + handler forwarded to the toast
   * fan-out (so e.g. a "Retry" button can sit on a connection-lost
   * notification). Drawer / browser routes ignore this field today.
   */
  readonly toastAction?: { label: string; onClick: () => void } | undefined
}

export interface NotificationsState {
  items: readonly NotificationItem[]
  unreadCount: number
  preferences: NotificationPreferences

  enqueue: (params: EnqueueParams) => string
  markRead: (id: string) => void
  markAllRead: () => void
  dismiss: (id: string) => void
  markReadBatch: (ids: readonly string[]) => void
  dismissBatch: (ids: readonly string[]) => void
  clearAll: () => void

  setRouteOverride: (
    category: NotificationCategory,
    routes: readonly NotificationRoute[],
  ) => void
  resetRouteOverride: (category: NotificationCategory) => void
  setGlobalMute: (muted: boolean) => void
  setBrowserPermission: (perm: NotificationPermission) => void

  handleWsEvent: (event: WsEvent) => void
}

export type NotificationsSet = StoreApi<NotificationsState>['setState']
export type NotificationsGet = StoreApi<NotificationsState>['getState']
