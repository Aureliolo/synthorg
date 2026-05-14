/**
 * Notifications store -- unified notification pipeline.
 *
 * WS events and frontend events flow through `enqueue()`, which
 * fans out to toast, drawer (store items), and browser Notification
 * API based on category routing config and user preferences.
 */

import { create } from 'zustand'

import { createLogger } from '@/lib/logger'
import * as browserNotifications from '@/services/browser-notifications'
import { useToastStore } from '@/stores/toast'
// Internal callers below (``enqueueXxx`` payload builders) use
// ``sanitizeWsString`` directly. The same symbol is re-exported
// further down so existing external imports from
// ``@/stores/notifications`` keep working unchanged.
import { sanitizeWsString } from '@/utils/ws-sanitize'
import type {
  NotificationCategory,
  NotificationItem,
  NotificationPreferences,
  NotificationRoute,
  NotificationSeverity,
} from '@/types/notifications'
import {
  CATEGORY_CONFIGS,
  DEFAULT_PREFERENCES,
  SEVERITY_TO_TOAST_VARIANT,
} from '@/types/notifications'
import type { WsEvent } from '@/api/types/websocket'
import { sanitizeForLog } from '@/utils/logging'
import { isObject } from '@/utils/type-guards'

const log = createLogger('notifications-store')

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const MAX_ITEMS = 200
const STALE_THRESHOLD_MS = 7 * 24 * 60 * 60 * 1000 // 7 days
const DEDUP_WINDOW_MS = 30_000
const PERSIST_DEBOUNCE_MS = 300
const STORAGE_KEY_ITEMS = 'so_notifications'
const STORAGE_KEY_PREFS = 'so_notification_prefs'

// ---------------------------------------------------------------------------
// Module-scoped state
// ---------------------------------------------------------------------------

// Module-scoped (escapes Zustand state) on purpose: the persist debounce timer must survive the store's set() boundary; cleanup is wired through cancelPendingPersist() from test-setup.tsx.
let persistTimer: ReturnType<typeof setTimeout> | null = null

// ---------------------------------------------------------------------------
// Enqueue params
// ---------------------------------------------------------------------------

export interface EnqueueParams {
  readonly category: NotificationCategory
  readonly title: string
  readonly description?: string
  readonly href?: string
  readonly entityId?: string
  readonly severity?: NotificationSeverity
  /**
   * Optional inline action label + handler forwarded to the toast
   * fan-out (so e.g. a "Retry" button can sit on a connection-lost
   * notification). Drawer / browser routes ignore this field today.
   */
  readonly toastAction?: { label: string; onClick: () => void }
}

// ---------------------------------------------------------------------------
// Store shape
// ---------------------------------------------------------------------------

interface NotificationsState {
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

  setRouteOverride: (category: NotificationCategory, routes: readonly NotificationRoute[]) => void
  resetRouteOverride: (category: NotificationCategory) => void
  setGlobalMute: (muted: boolean) => void
  setBrowserPermission: (perm: NotificationPermission) => void

  handleWsEvent: (event: WsEvent) => void
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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

function pruneStale(items: readonly NotificationItem[]): readonly NotificationItem[] {
  const cutoff = Date.now() - STALE_THRESHOLD_MS
  return items.filter((item) => new Date(item.timestamp).getTime() > cutoff)
}

const VALID_CATEGORIES = new Set(Object.keys(CATEGORY_CONFIGS))
const VALID_SEVERITIES: ReadonlySet<string> = new Set<NotificationSeverity>([
  'info',
  'warning',
  'error',
  'critical',
])

function isValidItem(item: unknown): item is NotificationItem {
  if (typeof item !== 'object' || item === null) return false
  const obj = item as Record<string, unknown>
  return (
    typeof obj.id === 'string' &&
    typeof obj.category === 'string' &&
    VALID_CATEGORIES.has(obj.category) &&
    typeof obj.severity === 'string' &&
    VALID_SEVERITIES.has(obj.severity) &&
    typeof obj.title === 'string' &&
    typeof obj.timestamp === 'string' &&
    typeof obj.read === 'boolean' &&
    Array.isArray(obj.dispatchedTo)
  )
}

function hydrateItems(): readonly NotificationItem[] {
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

function hydratePrefs(): NotificationPreferences {
  try {
    const raw = localStorage.getItem(STORAGE_KEY_PREFS)
    if (!raw) return DEFAULT_PREFERENCES
    const parsed = JSON.parse(raw) as unknown
    if (typeof parsed !== 'object' || parsed === null) return DEFAULT_PREFERENCES
    return { ...DEFAULT_PREFERENCES, ...(parsed as Partial<NotificationPreferences>) }
  } catch {
    return DEFAULT_PREFERENCES
  }
}

function debouncedPersist(state: NotificationsState): void {
  if (persistTimer !== null) clearTimeout(persistTimer)
  persistTimer = setTimeout(() => {
    try {
      localStorage.setItem(STORAGE_KEY_ITEMS, JSON.stringify(state.items))
      localStorage.setItem(STORAGE_KEY_PREFS, JSON.stringify(state.preferences))
    } catch {
      // QuotaExceededError -- silently ignore
    }
  }, PERSIST_DEBOUNCE_MS)
}

/**
 * Clear the pending debounce timer without flushing. Intended for tests
 * that enqueue notifications but finish before the persist interval
 * elapses; without this the timer outlives the test boundary and vitest's
 * --detect-async-leaks flags it.
 */
export function cancelPendingPersist(): void {
  if (persistTimer !== null) {
    clearTimeout(persistTimer)
    persistTimer = null
  }
}

function countUnread(items: readonly NotificationItem[]): number {
  return items.filter((i) => !i.read).length
}

// ``sanitizeWsString`` and ``MAX_STRING_LEN`` live in
// ``@/utils/ws-sanitize`` so benchmark + unit-test imports can pull
// them in without dragging this store's side effects (toast queue,
// persistence subscription, ``localStorage`` hydration) into the
// import graph. We re-export here so existing call sites that
// import from ``@/stores/notifications`` keep working unchanged.
export {
  MAX_WS_STRING_LEN as MAX_STRING_LEN,
  sanitizeWsEnum,
  sanitizeWsString,
} from '@/utils/ws-sanitize'

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

// Initialize nextId from hydrated items to prevent ID collisions
// after page reload (items persisted in localStorage retain their IDs).
let nextId = 0

export const useNotificationsStore = create<NotificationsState>()((set, get) => {
  const initialItems = hydrateItems()
  const initialPrefs = hydratePrefs()
  nextId = initialItems.reduce((max, item) => {
    const n = Number(item.id)
    return Number.isFinite(n) && n > max ? n : max
  }, 0)

  return {
    items: initialItems,
    unreadCount: countUnread(initialItems),
    preferences: initialPrefs,

    enqueue(params) {
      if (!VALID_CATEGORIES.has(params.category)) {
        log.warn('enqueue called with unknown category, ignored', {
          category: sanitizeForLog(params.category),
        })
        return ''
      }
      const severity = params.severity ?? CATEGORY_CONFIGS[params.category].severity
      const prefs = get().preferences
      const routes = computeRoutes(params.category, prefs)
      const now = new Date().toISOString()

      // Deduplicate by category + entityId within the window
      if (params.entityId) {
        const existing = get().items.find(
          (item) =>
            !item.read &&
            item.category === params.category &&
            item.entityId === params.entityId &&
            Date.now() - new Date(item.timestamp).getTime() < DEDUP_WINDOW_MS,
        )
        if (existing) {
          // Bump existing item to the top with updated timestamp
          set((state) => {
            const updated = state.items.map((item) =>
              item.id === existing.id
                ? { ...item, timestamp: now }
                : item,
            )
            const sorted = [
              updated.find((i) => i.id === existing.id)!,
              ...updated.filter((i) => i.id !== existing.id),
            ]
            return { items: sorted }
          })
          debouncedPersist(get())
          return existing.id
        }
      }

      const id = String(++nextId)
      const item: NotificationItem = {
        id,
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

      set((state) => {
        const newItems = [item, ...state.items].slice(0, MAX_ITEMS)
        return {
          items: newItems,
          unreadCount: countUnread(newItems),
        }
      })

      // Fan out to toast
      if (routes.includes('toast')) {
        useToastStore.getState().add({
          variant: SEVERITY_TO_TOAST_VARIANT[severity],
          title: params.title,
          description: params.description,
          action: params.toastAction,
        })
      }

      // Fan out to browser notifications
      if (routes.includes('browser')) {
        browserNotifications.show({
          title: params.title,
          body: params.description,
          href: params.href,
          tag: params.entityId,
        })
      }

      debouncedPersist(get())
      return id
    },

    markRead(id) {
      set((state) => {
        const items = state.items.map((item) =>
          item.id === id ? { ...item, read: true } : item,
        )
        return { items, unreadCount: countUnread(items) }
      })
      debouncedPersist(get())
    },

    markAllRead() {
      set((state) => {
        const items = state.items.map((item) => ({ ...item, read: true }))
        return { items, unreadCount: 0 }
      })
      debouncedPersist(get())
    },

    dismiss(id) {
      set((state) => {
        const items = state.items.filter((item) => item.id !== id)
        return { items, unreadCount: countUnread(items) }
      })
      debouncedPersist(get())
    },

    markReadBatch(ids: readonly string[]) {
      set((state) => {
        const idSet = new Set(ids)
        const updated = state.items.map((item) =>
          idSet.has(item.id) ? { ...item, read: true } : item,
        )
        return { items: updated, unreadCount: countUnread(updated) }
      })
      debouncedPersist(get())
    },

    dismissBatch(ids: readonly string[]) {
      set((state) => {
        const idSet = new Set(ids)
        const items = state.items.filter((item) => !idSet.has(item.id))
        return { items, unreadCount: countUnread(items) }
      })
      debouncedPersist(get())
    },

    clearAll() {
      set({ items: [], unreadCount: 0 })
      debouncedPersist(get())
    },

    setRouteOverride(category, routes) {
      set((state) => ({
        preferences: {
          ...state.preferences,
          routeOverrides: {
            ...state.preferences.routeOverrides,
            [category]: routes,
          },
        },
      }))
      debouncedPersist(get())
    },

    resetRouteOverride(category) {
      set((state) => {
        const { [category]: _removed, ...rest } = state.preferences.routeOverrides
        void _removed
        return {
          preferences: {
            ...state.preferences,
            routeOverrides: rest,
          },
        }
      })
      debouncedPersist(get())
    },

    setGlobalMute(muted) {
      set((state) => ({
        preferences: { ...state.preferences, globalMute: muted },
      }))
      debouncedPersist(get())
    },

    setBrowserPermission(perm) {
      set((state) => ({
        preferences: { ...state.preferences, browserPermission: perm },
      }))
      debouncedPersist(get())
    },

    handleWsEvent(event) {
      const { enqueue } = get()
      // Narrow ``event.payload`` (typed Record<string, unknown> on the
      // wire but a malformed broker can still send anything) in one
      // step instead of an unsafe ``as`` followed by a manual typeof.
      if (!isObject(event.payload)) {
        log.warn('Notification WS event has invalid payload', {
          eventType: sanitizeForLog(String(event.event_type)),
        })
        return
      }
      const payload = event.payload

      switch (event.event_type) {
        case 'approval.submitted':
          enqueue({
            category: 'approvals.pending',
            title: 'Approval requested',
            description: sanitizeWsString(payload.title),
            href: typeof payload.approval_id === 'string' ? `/approvals` : undefined,
            entityId: sanitizeWsString(payload.approval_id),
          })
          break

        case 'approval.expired':
          enqueue({
            category: 'approvals.expiring',
            title: 'Approval expiring',
            entityId: sanitizeWsString(payload.approval_id),
          })
          break

        case 'approval.approved':
          enqueue({
            category: 'approvals.decided',
            title: 'Approval approved',
            entityId: sanitizeWsString(payload.approval_id),
          })
          break

        case 'approval.rejected':
          enqueue({
            category: 'approvals.decided',
            title: 'Approval rejected',
            entityId: sanitizeWsString(payload.approval_id),
          })
          break

        case 'budget.alert': {
          const level = typeof payload.level === 'string' ? payload.level : 'threshold'
          const isExhausted = level === 'exhausted' || level === 'hard_stop'
          enqueue({
            category: isExhausted ? 'budget.exhausted' : 'budget.threshold',
            title: isExhausted ? 'Budget exhausted' : 'Budget threshold crossed',
            description: sanitizeWsString(payload.message),
            severity: isExhausted ? 'critical' : 'warning',
          })
          break
        }

        case 'system.error':
          enqueue({
            category: 'system.error',
            title: 'System error',
            description: sanitizeWsString(payload.message),
          })
          break

        case 'system.shutdown':
          enqueue({
            category: 'system.shutdown',
            title: 'System shutting down',
          })
          break

        case 'personality.trimmed': {
          const agentName = sanitizeWsString(payload.agent_name, 64)
          enqueue({
            category: 'agents.personality_trimmed',
            title: 'Personality trimmed',
            description: agentName ? `${agentName} personality was trimmed` : undefined,
            entityId: sanitizeWsString(payload.agent_id),
          })
          break
        }

        case 'agent.hired':
          enqueue({
            category: 'agents.hired',
            title: 'Agent hired',
            description: sanitizeWsString(payload.agent_name, 64),
            entityId: sanitizeWsString(payload.agent_id),
          })
          break

        case 'agent.fired':
          enqueue({
            category: 'agents.fired',
            title: 'Agent fired',
            description: sanitizeWsString(payload.agent_name, 64),
            entityId: sanitizeWsString(payload.agent_id),
          })
          break

        case 'task.status_changed': {
          const status = typeof payload.status === 'string' ? payload.status : ''
          const taskId = sanitizeWsString(payload.task_id)
          const title = sanitizeWsString(payload.title)
          if (status === 'failed') {
            enqueue({
              category: 'tasks.failed',
              title: 'Task failed',
              description: title,
              entityId: taskId,
              href: taskId ? `/tasks` : undefined,
            })
          } else if (status === 'blocked') {
            enqueue({
              category: 'tasks.blocked',
              title: 'Task blocked',
              description: title,
              entityId: taskId,
            })
          }
          break
        }

        default:
          // Unhandled event types are silently ignored
          break
      }
    },
  }
})
