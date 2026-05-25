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
import type { NotificationsState } from './types'

const STALE_THRESHOLD_MS = 7 * 24 * 60 * 60 * 1000 // 7 days
const PERSIST_DEBOUNCE_MS = 300
const STORAGE_KEY_ITEMS = 'so_notifications'
const STORAGE_KEY_PREFS = 'so_notification_prefs'

const VALID_CATEGORIES = new Set(Object.keys(CATEGORY_CONFIGS))
const VALID_SEVERITIES: ReadonlySet<string> = new Set([
  'info',
  'warning',
  'error',
  'critical',
])
const VALID_ROUTES: ReadonlySet<string> = new Set([
  'drawer',
  'toast',
  'browser',
])

// Module-scoped (escapes Zustand state) on purpose: the persist
// debounce timer must survive the store's set() boundary; cleanup
// is wired through cancelPendingPersist() from test-setup.tsx.
let persistTimer: ReturnType<typeof setTimeout> | null = null

export function pruneStale(
  items: readonly NotificationItem[],
): readonly NotificationItem[] {
  const cutoff = Date.now() - STALE_THRESHOLD_MS
  return items.filter((item) => new Date(item.timestamp).getTime() > cutoff)
}

const ITEM_REQUIRED_STRING_FIELDS = [
  'id',
  'category',
  'severity',
  'title',
  'timestamp',
] as const

function isValidDispatchedTo(value: unknown): boolean {
  if (!Array.isArray(value)) return false
  // Validate each entry is a known route string; corrupt
  // localStorage entries shouldn't rehydrate into NotificationItem.
  return value.every(
    (entry) => typeof entry === 'string' && VALID_ROUTES.has(entry),
  )
}

function isValidItem(item: unknown): item is NotificationItem {
  if (typeof item !== 'object' || item === null) return false
  const obj = item as Record<string, unknown>
  for (const field of ITEM_REQUIRED_STRING_FIELDS) {
    if (typeof obj[field] !== 'string') return false
  }
  return (
    VALID_CATEGORIES.has(obj.category as string)
    && VALID_SEVERITIES.has(obj.severity as string)
    && typeof obj.read === 'boolean'
    && isValidDispatchedTo(obj.dispatchedTo)
  )
}

export function hydrateItems(): readonly NotificationItem[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY_ITEMS)
    if (!raw) return []
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed)) return []
    return pruneStale(parsed.filter(isValidItem))
  } catch {
    return []
  }
}

function sanitizeRouteOverrides(
  raw: unknown,
): NotificationPreferences['routeOverrides'] {
  if (typeof raw !== 'object' || raw === null) return {}
  const out: Partial<Record<NotificationCategory, readonly NotificationRoute[]>>
    = {}
  for (const [category, routes] of Object.entries(raw)) {
    if (!VALID_CATEGORIES.has(category)) continue
    if (!Array.isArray(routes)) continue
    if (!routes.every((r) => typeof r === 'string' && VALID_ROUTES.has(r))) {
      continue
    }
    out[category as NotificationCategory] = routes as readonly NotificationRoute[]
  }
  return out
}

export function hydratePrefs(): NotificationPreferences {
  try {
    const raw = localStorage.getItem(STORAGE_KEY_PREFS)
    if (!raw) return DEFAULT_PREFERENCES
    const parsed = JSON.parse(raw) as unknown
    if (typeof parsed !== 'object' || parsed === null) {
      return DEFAULT_PREFERENCES
    }
    const candidate = parsed as Partial<NotificationPreferences>
    return {
      ...DEFAULT_PREFERENCES,
      ...candidate,
      // Validate the deserialized routeOverrides map: drop unknown
      // categories and non-allowlisted route strings before merging
      // so corrupt or stale localStorage data can't crash route
      // handling later at runtime.
      routeOverrides: sanitizeRouteOverrides(candidate.routeOverrides),
    }
  } catch {
    return DEFAULT_PREFERENCES
  }
}

export function debouncedPersist(state: NotificationsState): void {
  if (persistTimer !== null) clearTimeout(persistTimer)
  persistTimer = setTimeout(() => {
    try {
      localStorage.setItem(STORAGE_KEY_ITEMS, JSON.stringify(state.items))
      localStorage.setItem(
        STORAGE_KEY_PREFS,
        JSON.stringify(state.preferences),
      )
    } catch {
      // QuotaExceededError -- silently ignore
    }
  }, PERSIST_DEBOUNCE_MS)
}

/**
 * Clear the pending debounce timer without flushing. Intended for tests
 * that enqueue notifications but finish before the persist interval
 * elapses; without this the timer outlives the test boundary and the
 * active-handle gate fails the test.
 */
export function cancelPendingPersist(): void {
  if (persistTimer !== null) {
    clearTimeout(persistTimer)
    persistTimer = null
  }
}

export function countUnread(items: readonly NotificationItem[]): number {
  return items.filter((i) => !i.read).length
}

// Module-scoped nextId persisted across enqueue calls. Initialised
// against hydrated items at store-create time (see aggregator) so
// post-reload IDs don't collide with stored ones.
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
