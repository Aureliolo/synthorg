/**
 * Leaf module that breaks the static cycle between `api/client` and
 * `stores/auth`. The client publishes a 401 signal here; the auth store
 * subscribes at module init. Neither module imports the other.
 */

type UnauthorizedHandler = () => void

let current: UnauthorizedHandler | null = null

export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  current = handler
}

export function notifyUnauthorized(): void {
  current?.()
}

export function _resetUnauthorizedHandlerForTests(): void {
  current = null
}
