/**
 * Browser Notification API service.
 *
 * Standalone module (not a React hook). Handles permission flow,
 * rate limiting, visibility checks, and click-to-focus navigation.
 */

import { createLogger } from '@/lib/logger'

const log = createLogger('browser-notifications')

// ---------------------------------------------------------------------------
// Rate limiting
// ---------------------------------------------------------------------------

const MAX_NOTIFICATIONS = 3
const WINDOW_MS = 10_000
// Hard cap on the in-memory ring so the array cannot grow unbounded
// even if a future caller invokes ``recordNotification`` without
// going through ``isRateLimited`` first (the only current path
// already prunes per call). 2x the per-window quota leaves ample
// headroom for the prune-on-call pattern while bounding worst-case
// memory at a handful of entries.
const MAX_TIMESTAMPS_BUFFER = MAX_NOTIFICATIONS * 2
const recentTimestamps: number[] = []

function isRateLimited(): boolean {
  const now = Date.now()
  // Prune timestamps outside the window
  while (recentTimestamps.length > 0 && now - recentTimestamps[0]! > WINDOW_MS) {
    recentTimestamps.shift()
  }
  return recentTimestamps.length >= MAX_NOTIFICATIONS
}

function recordNotification(): void {
  recentTimestamps.push(Date.now())
  while (recentTimestamps.length > MAX_TIMESTAMPS_BUFFER) {
    recentTimestamps.shift()
  }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export function isSupported(): boolean {
  return 'Notification' in window
}

export function getPermission(): NotificationPermission {
  if (!isSupported()) return 'denied'
  return Notification.permission
}

export async function requestPermission(): Promise<NotificationPermission> {
  if (!isSupported()) return 'denied'
  try {
    return await Notification.requestPermission()
  } catch {
    log.warn('Failed to request notification permission')
    return 'denied'
  }
}

export interface BrowserNotificationPayload {
  readonly title: string
  readonly body?: string
  readonly href?: string
  readonly tag?: string
}

export function show(payload: BrowserNotificationPayload): void {
  if (!isSupported()) return
  if (Notification.permission !== 'granted') return

  // Only fire when the tab is backgrounded -- if the user is looking
  // at the dashboard, the toast is sufficient.
  if (document.visibilityState === 'visible') return

  if (isRateLimited()) {
    log.debug('Browser notification rate-limited')
    return
  }

  try {
    const notification = new Notification(payload.title, {
      body: payload.body,
      icon: '/favicon.svg',
      tag: payload.tag,
    })
    recordNotification()

    notification.onclick = () => {
      window.focus()
      notification.close()
      if (payload.href) {
        window.dispatchEvent(
          new CustomEvent('notification-navigate', {
            detail: { href: payload.href },
          }),
        )
      }
    }
  } catch {
    log.warn('Failed to create browser notification')
  }
}
