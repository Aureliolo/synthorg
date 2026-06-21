/**
 * Per-endpoint circuit breaker for the transparent 429 retry transports.
 *
 * When the same endpoint terminally rate-limits repeatedly, continuing to
 * fire requests at it only deepens the back-pressure: an unguarded retry
 * loop can issue thousands of requests against a single 429-ing endpoint.
 * After a run of consecutive failures the breaker OPENS for a cooldown
 * window; while open, callers short-circuit instead of issuing another
 * doomed request. A success closes it.
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
  /**
   * True while a single half-open probe has been admitted but not yet
   * resolved by recordSuccess / recordFailure. Blocks every other caller
   * so the cooldown releases exactly ONE probe, not a concurrent burst.
   */
  halfOpenProbeInFlight: boolean
}

const states = new Map<string, EndpointState>()

function stateFor(key: string): EndpointState {
  const existing = states.get(key)
  if (existing) return existing
  const fresh: EndpointState = { failures: 0, openedAt: null, halfOpenProbeInFlight: false }
  states.set(key, fresh)
  return fresh
}

/**
 * Whether the breaker for *key* is currently open (requests should
 * short-circuit). Once the cooldown elapses the breaker transitions to
 * half-open: the FIRST read clears the open flag, admits a single probe
 * (returns `false`), and blocks every concurrent caller (returns `true`)
 * until that probe resolves. A failed probe re-opens the breaker at once;
 * a successful one closes it.
 */
export function isCircuitOpen(key: string): boolean {
  const state = states.get(key)
  if (!state || state.openedAt === null) return state?.halfOpenProbeInFlight ?? false
  if (Date.now() - state.openedAt >= OPEN_COOLDOWN_MS) {
    if (state.halfOpenProbeInFlight) return true
    state.openedAt = null
    state.halfOpenProbeInFlight = true
    return false
  }
  return true
}

/** Record a terminal failure (exhausted 429) for *key*; may open the breaker. */
export function recordFailure(key: string): void {
  const state = stateFor(key)
  if (state.halfOpenProbeInFlight) {
    // A failed half-open probe re-trips the breaker immediately, without
    // waiting for another full FAILURE_THRESHOLD run.
    state.halfOpenProbeInFlight = false
    state.failures = FAILURE_THRESHOLD
    state.openedAt = Date.now()
    log.warn('circuit_opened', { endpoint: sanitizeForLog(key) })
    return
  }
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
  state.halfOpenProbeInFlight = false
}

/**
 * Clear all breaker state. Wired into the global test `afterEach` so a
 * tripped breaker in one test can never leak into the next.
 */
export function resetCircuitBreaker(): void {
  states.clear()
}

// Reset on Vite HMR so a breaker tripped by a dev-time 429 burst doesn't
// persist (phantom "open") across an edit-reload and silently short-circuit
// real requests. No-op in production (import.meta.hot is undefined).
if (import.meta.hot) {
  import.meta.hot.dispose(() => states.clear())
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
