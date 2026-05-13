import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { useToastStore } from '@/stores/toast'

const log = createLogger('global-error')

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
 * The handlers log via the structured logger so observability
 * pipelines can sample them, and surface a low-noise toast in
 * production so operators see SOMETHING when an async path drops
 * without explicit error UX in the calling component.
 */
let installed = false

export function installGlobalErrorHandlers(): void {
  if (installed) return
  if (typeof window === 'undefined') return
  installed = true

  window.addEventListener('unhandledrejection', (event: PromiseRejectionEvent) => {
    const reason = event.reason
    log.error('Unhandled promise rejection', {
      reason: sanitizeForLog(formatReason(reason)),
    })
    notifyOperator('Background task failed', formatReason(reason))
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
    log.error('Uncaught global error', {
      message: sanitizeForLog(event.message),
      filename: sanitizeForLog(event.filename),
      lineno: event.lineno,
      colno: event.colno,
      reason: sanitizeForLog(reason),
    })
    notifyOperator('Background task failed', reason)
  })
}

function formatReason(reason: unknown): string {
  if (reason instanceof Error) return reason.message
  if (typeof reason === 'string') return reason
  try {
    return JSON.stringify(reason)
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
  try {
    useToastStore.getState().add({
      variant: 'warning',
      title,
      description,
    })
  } catch {
    // Toast store may not be hydrated yet during the earliest bootstrap
    // failures; swallow so the handler itself never throws.
  }
}
