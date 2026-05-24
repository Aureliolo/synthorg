import { createLogger } from '@/lib/logger'
import * as browserNotifications from '@/services/browser-notifications'
import { useToastStore } from '@/stores/toast'
import { sanitizeForLog } from '@/utils/logging'
import type {
  NotificationCategory,
  NotificationItem,
  NotificationPreferences,
  NotificationRoute,
} from '@/types/notifications'
import {
  CATEGORY_CONFIGS,
  SEVERITY_TO_TOAST_VARIANT,
} from '@/types/notifications'
import {
  VALID_CATEGORIES,
  allocateNotificationId,
  countUnread,
  debouncedPersist,
} from './persistence'
import type {
  EnqueueParams,
  NotificationsGet,
  NotificationsSet,
} from './types'

const log = createLogger('notifications-store')

const MAX_ITEMS = 200
const DEDUP_WINDOW_MS = 30_000

function computeRoutes(
  category: NotificationCategory,
  prefs: NotificationPreferences,
): readonly NotificationRoute[] {
  const overrides = prefs.routeOverrides[category]
  const routes = overrides ?? CATEGORY_CONFIGS[category].defaultRoutes
  if (prefs.globalMute) {
    return routes.filter((r) => r === 'drawer')
  }
  return routes
}

function findDedupableItem(
  items: readonly NotificationItem[],
  params: EnqueueParams,
): NotificationItem | undefined {
  if (!params.entityId) return undefined
  return items.find(
    (item) =>
      !item.read
      && item.category === params.category
      && item.entityId === params.entityId
      && Date.now() - new Date(item.timestamp).getTime() < DEDUP_WINDOW_MS,
  )
}

function bumpDedupedItem(
  set: NotificationsSet,
  existingId: string,
  now: string,
): void {
  set((state) => {
    const updated = state.items.map((item) =>
      item.id === existingId ? { ...item, timestamp: now } : item,
    )
    const target = updated.find((i) => i.id === existingId)!
    const sorted = [target, ...updated.filter((i) => i.id !== existingId)]
    return { items: sorted }
  })
}

function buildNewItem(
  params: EnqueueParams,
  routes: readonly NotificationRoute[],
  now: string,
): NotificationItem {
  const severity = params.severity
    ?? CATEGORY_CONFIGS[params.category].severity
  return {
    id: allocateNotificationId(),
    category: params.category,
    severity,
    title: params.title,
    description: params.description,
    timestamp: now,
    read: false,
    href: params.href,
    entityId: params.entityId,
    dispatchedTo: routes,
  }
}

function fanOutToBrowserAndToast(
  params: EnqueueParams,
  routes: readonly NotificationRoute[],
  item: NotificationItem,
): void {
  if (routes.includes('toast')) {
    useToastStore.getState().add({
      variant: SEVERITY_TO_TOAST_VARIANT[item.severity],
      title: params.title,
      description: params.description,
      action: params.toastAction,
    })
  }
  if (routes.includes('browser')) {
    browserNotifications.show({
      title: params.title,
      body: params.description,
      href: params.href,
      tag: params.entityId,
    })
  }
}

function enqueueImpl(
  set: NotificationsSet,
  get: NotificationsGet,
  params: EnqueueParams,
): string {
  if (!VALID_CATEGORIES.has(params.category)) {
    log.warn('enqueue called with unknown category, ignored', {
      category: sanitizeForLog(params.category),
    })
    return ''
  }
  const prefs = get().preferences
  const routes = computeRoutes(params.category, prefs)
  const now = new Date().toISOString()

  // Deduplicate by category + entityId within the window.
  const existing = findDedupableItem(get().items, params)
  if (existing) {
    bumpDedupedItem(set, existing.id, now)
    debouncedPersist(get())
    return existing.id
  }

  const item = buildNewItem(params, routes, now)
  set((state) => {
    const newItems = [item, ...state.items].slice(0, MAX_ITEMS)
    return { items: newItems, unreadCount: countUnread(newItems) }
  })
  fanOutToBrowserAndToast(params, routes, item)
  debouncedPersist(get())
  return item.id
}

export function createEnqueueAction(
  set: NotificationsSet,
  get: NotificationsGet,
) {
  return {
    enqueue: (params: EnqueueParams) => enqueueImpl(set, get, params),
  }
}
