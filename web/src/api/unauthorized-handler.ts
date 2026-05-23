/**
 * Leaf module that breaks the static cycle between `api/client` and
 * `stores/auth`. The client publishes a 401 signal here; subscribers
 * register at module init. Neither side imports the other.
 *
 * Multi-subscriber Set so independent modules (auth store today, a
 * separate observability subscriber tomorrow) can both react to 401s
 * without silently overwriting each other.
 */

import { createLogger } from '@/lib/logger'

type UnauthorizedHandler = () => void

const log = createLogger('unauthorized-handler')

const subscribers = new Set<UnauthorizedHandler>()

/**
 * Register a 401 handler. Returns an unsubscribe function -- callers
 * that need to detach (HMR, teardown) hold the returned closure;
 * module-init wiring can ignore it.
 */
export function setUnauthorizedHandler(handler: UnauthorizedHandler): () => void {
  subscribers.add(handler)
  return () => {
    subscribers.delete(handler)
  }
}

/**
 * Fan a 401 signal out to every subscriber. When nothing is wired
 * (early bootstrap, standalone tools, test environments that import
 * `api/client` without `stores/auth`), log a warning so session
 * expiry is never silently swallowed -- the previous refactor's
 * defensive `window.location.href` fallback is replaced by an
 * observable log line that surfaces the misconfiguration without
 * coupling this leaf module back to the DOM.
 */
export function notifyUnauthorized(): void {
  if (subscribers.size === 0) {
    log.warn('unauthorized.no_handler', {
      detail: '401 received but no handler registered; session expiry not propagated',
    })
    return
  }
  for (const handler of subscribers) {
    try {
      handler()
    } catch (err) {
      // A throwing subscriber must not prevent the others from running.
      log.error('unauthorized.handler_threw', err)
    }
  }
}

export function _resetUnauthorizedHandlerForTests(): void {
  subscribers.clear()
}
