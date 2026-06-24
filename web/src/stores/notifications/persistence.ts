import { getNamespaceSettings, updateSetting } from '@/api/endpoints/settings'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import type {
  NotificationCategory,
  NotificationItem,
  NotificationPreferences,
  NotificationRoute,
} from '@/types/notifications'
import {
  CATEGORY_CONFIGS,
  DEFAULT_PREFERENCES,
} from '@/types/notifications'

const log = createLogger('notifications-persistence')

// Backend settings key holding the routing preferences as a JSON blob. The
// dashboard is a pure API consumer: routing preferences live in the
// ``notifications`` settings namespace, not the browser.
const PREFS_SETTING_KEY = 'preferences'

const VALID_CATEGORIES = new Set(Object.keys(CATEGORY_CONFIGS))

/** Narrow a raw key to ``NotificationCategory`` by membership in the config. */
function isNotificationCategory(value: string): value is NotificationCategory {
  return VALID_CATEGORIES.has(value)
}

const VALID_ROUTES: ReadonlySet<string> = new Set([
  'drawer',
  'toast',
  'browser',
])

function sanitizeRouteOverrides(
  raw: unknown,
): NotificationPreferences['routeOverrides'] {
  if (typeof raw !== 'object' || raw === null) return {}
  const out: Partial<Record<NotificationCategory, readonly NotificationRoute[]>>
    = {}
  for (const [category, routes] of Object.entries(raw)) {
    if (!isNotificationCategory(category)) continue
    if (!Array.isArray(routes)) continue
    if (!routes.every((r) => typeof r === 'string' && VALID_ROUTES.has(r))) {
      continue
    }
    out[category] = routes as readonly NotificationRoute[]
  }
  return out
}

/**
 * Notification drawer items are an ephemeral, session-only buffer of the live
 * WebSocket notification stream -- they are never persisted client-side (the
 * backend is the source of truth and the Activity feed is the durable history).
 * The store seeds empty and accumulates from live events.
 */
export function hydrateItems(): readonly NotificationItem[] {
  return []
}

/**
 * Load notification-routing preferences from the backend ``notifications``
 * settings namespace. ``browserPermission`` is per-device and is NOT stored
 * backend-side, so it is left at the default here and re-synced at runtime
 * from the browser Notification API. Degrades to defaults on failure.
 */
export async function hydratePreferences(): Promise<NotificationPreferences> {
  try {
    const entries = await getNamespaceSettings('notifications')
    const entry = entries.find((e) => e.definition.key === PREFS_SETTING_KEY)
    if (entry === undefined || entry.value === '') return DEFAULT_PREFERENCES
    const parsed = JSON.parse(entry.value) as unknown
    if (typeof parsed !== 'object' || parsed === null) return DEFAULT_PREFERENCES
    const candidate = parsed as Partial<NotificationPreferences>
    return {
      ...DEFAULT_PREFERENCES,
      globalMute: candidate.globalMute === true,
      routeOverrides: sanitizeRouteOverrides(candidate.routeOverrides),
    }
  } catch (err) {
    log.warn('Failed to hydrate notification preferences, using defaults:', getErrorMessage(err))
    return DEFAULT_PREFERENCES
  }
}

/**
 * Persist the routing preferences to the backend. Only the backend-owned
 * fields (route overrides + global mute) are written; the per-device browser
 * permission is excluded. Toasts on failure (store-mutation contract).
 */
export async function persistPreferences(prefs: NotificationPreferences): Promise<void> {
  try {
    await updateSetting('notifications', PREFS_SETTING_KEY, {
      value: JSON.stringify({
        routeOverrides: prefs.routeOverrides,
        globalMute: prefs.globalMute,
      }),
    })
  } catch (err) {
    log.error('Failed to save notification preferences:', getErrorMessage(err))
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Failed to save notification preferences'),
      description: getErrorMessage(err),
    })
  }
}

export function countUnread(items: readonly NotificationItem[]): number {
  return items.filter((i) => !i.read).length
}

// Module-scoped nextId persisted across enqueue calls within a session.
let nextId = 0

export function setNextIdFromHydrated(
  items: readonly NotificationItem[],
): void {
  nextId = items.reduce((max, item) => {
    const n = Number(item.id)
    return Number.isFinite(n) && n > max ? n : max
  }, 0)
}

export function allocateNotificationId(): string {
  nextId += 1
  return String(nextId)
}

export { VALID_CATEGORIES }
