/**
 * Per-endpoint circuit breaker for the transparent 429 retry transports.
 *
 * When the same endpoint terminally rate-limits repeatedly, continuing to
 * fire requests at it only deepens the back-pressure (the storm that
 * motivated #2438 fired 4,006 GETs at a single 429-ing endpoint). After a
 * run of consecutive failures the breaker OPENS for a cooldown window;
 * while open, callers short-circuit instead of issuing another doomed
 * request. A success closes it.
 *
 * State is timestamp-based (NO timers), so the breaker never holds the
 * event loop open and stays invisible to the active-handle gate. The
 * cooldown is evaluated lazily on the next `isOpen` read.
 *
 * Keyed by endpoint (request path), so one hot endpoint tripping does not
 * starve unrelated traffic. Both HTTP transports participate: the axios
 * interceptor (`@/api/client`) and the raw-fetch helper
 * (`@/utils/fetch-with-retry`).
 */

import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'

const log = createLogger('circuit-breaker')

/** Consecutive terminal failures on one endpoint before the breaker opens. */
const FAILURE_THRESHOLD = 5

/** How long the breaker stays open before a half-open probe is allowed (ms). */
const OPEN_COOLDOWN_MS = 10_000

interface EndpointState {
  failures: number
  /** Wall-clock ms when the breaker opened; `null` while closed. */
  openedAt: number | null
}

const states = new Map<string, EndpointState>()

function stateFor(key: string): EndpointState {
  const existing = states.get(key)
  if (existing) return existing
  const fresh: EndpointState = { failures: 0, openedAt: null }
  states.set(key, fresh)
  return fresh
}

/**
 * Whether the breaker for *key* is currently open (requests should
 * short-circuit). Once the cooldown elapses the breaker transitions to
 * half-open: this read clears the open state and returns `false`, letting
 * a single probe through. A fresh `recordFailure` re-opens it.
 */
export function isCircuitOpen(key: string): boolean {
  const state = states.get(key)
  if (!state || state.openedAt === null) return false
  if (Date.now() - state.openedAt >= OPEN_COOLDOWN_MS) {
    state.openedAt = null
    state.failures = 0
    return false
  }
  return true
}

/** Record a terminal failure (exhausted 429) for *key*; may open the breaker. */
export function recordFailure(key: string): void {
  const state = stateFor(key)
  state.failures += 1
  if (state.failures >= FAILURE_THRESHOLD && state.openedAt === null) {
    state.openedAt = Date.now()
    log.warn('circuit_opened', { endpoint: sanitizeForLog(key) })
  }
}

/** Record a successful (non-429) response for *key*; closes the breaker. */
export function recordSuccess(key: string): void {
  const state = states.get(key)
  if (!state) return
  state.failures = 0
  state.openedAt = null
}

/**
 * Clear all breaker state. Wired into the global test `afterEach` so a
 * tripped breaker in one test can never leak into the next.
 */
export function resetCircuitBreaker(): void {
  states.clear()
}

/**
 * Best-effort endpoint key from a request target. Uses the URL path so
 * query strings / origins don't fragment the key (every
 * `/providers/presets` shares one breaker).
 */
export function circuitKeyFromUrl(url: string | undefined): string {
  if (!url) return '<unknown>'
  try {
    return new URL(url, 'http://x').pathname
  } catch {
    return url
  }
}
