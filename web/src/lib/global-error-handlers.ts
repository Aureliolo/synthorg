import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { useToastStore } from '@/stores/toast'

const log = createLogger('global-error')

const TOAST_TITLE = 'Unexpected client error'

// Patterns the W3C / browsers fire as warnings that are NOT actionable
// failures. ResizeObserver loop is benign per the spec: it reports that
// a callback mutated layout, triggering another resize before the next
// paint, and is the browser's way of asking the page to defer the
// follow-up resize, not a crash. Hydration / chunk-load patterns are
// the React + Vite equivalents: extremely common in dev, recoverable
// by a reload, and not worth waking the operator over.
const BENIGN_ERROR_PATTERNS: readonly RegExp[] = [
  /^ResizeObserver loop /,
  /^Hydration failed because /,
  /^Text content does not match server-rendered HTML/,
  /^Hydration completed but contains mismatches/,
  /Loading chunk \d+ failed/,
  /Failed to fetch dynamically imported module/,
]

export function isBenignError(reason: unknown): boolean {
  const msg = reason instanceof Error
    ? reason.message
    : typeof reason === 'string' ? reason : null
  if (!msg) return false
  return BENIGN_ERROR_PATTERNS.some((pattern) => pattern.test(msg))
}

/**
 * Install window-level handlers for unhandled async failures.
 *
 * React's error boundaries catch render-phase errors but they cannot
 * see promise rejections that escape a component (a `useEffect`'s
 * `await` that never lands in a `try/catch`, a stray `void
 * doStuff()` whose continuation throws). Browsers route those to
 * `window.onunhandledrejection`; without an explicit handler the
 * error is silently logged to the dev console and disappears in
 * production. Same story for synchronous errors that bubble out of
 * `requestAnimationFrame` / `setTimeout` callbacks.
 *
 * Benign browser warnings (see `BENIGN_ERROR_PATTERNS`) are filtered
 * to DEBUG so they do not toast or pollute the error sink. Everything
 * else logs via the structured logger and surfaces a low-noise toast
 * in production.
 */
let installed = false

export function installGlobalErrorHandlers(): void {
  if (installed) return
  if (typeof window === 'undefined') return
  installed = true

  window.addEventListener('unhandledrejection', (event: PromiseRejectionEvent) => {
    const reason = event.reason
    const formatted = formatReason(reason)
    if (isBenignError(reason)) {
      log.debug('Benign promise rejection ignored', {
        reason: sanitizeForLog(formatted),
      })
      return
    }
    log.error('Unhandled promise rejection', {
      reason: sanitizeForLog(formatted),
    })
    notifyOperator(TOAST_TITLE, formatted)
  })

  window.addEventListener('error', (event: ErrorEvent) => {
    // Some browser errors only carry message / filename / lineno /
    // colno and have a null ``event.error`` (cross-origin script
    // failures, certain resource-load errors, etc.). Falling back to
    // ``event.message`` keeps those visible in the observability
    // pipeline instead of dropping the signal on the floor.
    const reason = event.error != null
      ? formatReason(event.error)
      : (event.message || 'Uncaught global error')
    if (isBenignError(event.error ?? event.message)) {
      log.debug('Benign global error ignored', {
        reason: sanitizeForLog(reason),
      })
      return
    }
    log.error('Uncaught global error', {
      message: sanitizeForLog(event.message),
      filename: sanitizeForLog(event.filename),
      lineno: event.lineno,
      colno: event.colno,
      reason: sanitizeForLog(reason),
    })
    notifyOperator(TOAST_TITLE, reason)
  })
}

function formatReason(reason: unknown): string {
  if (reason instanceof Error) return reason.message
  if (typeof reason === 'string') return reason
  try {
    // ``JSON.stringify(undefined)`` returns ``undefined`` (not a string),
    // which would slip past the declared ``string`` return type and
    // surface as a runtime ``undefined`` in the operator toast.
    const serialized = JSON.stringify(reason)
    return serialized ?? String(reason)
  } catch {
    return String(reason)
  }
}

function notifyOperator(title: string, description: string): void {
  // Dev builds already surface the unhandled error in the console
  // and React DevTools; a toast on top of that is just noise. In
  // production the only signal would otherwise be the structured
  // log, which most operators don't tail in real time.
  if (import.meta.env?.DEV) return
  // The toast store owns mutation error UX (per the Zustand store
  // mutation contract); callers do not wrap ``add`` in try/catch.
  useToastStore.getState().add({
    variant: 'warning',
    title,
    description,
  })
}
