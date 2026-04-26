/**
 * CSRF token utilities.
 *
 * The backend sets a non-HttpOnly `csrf_token` cookie on login/setup.
 * All mutating requests (POST/PUT/PATCH/DELETE) must include an
 * `X-CSRF-Token` header whose value matches this cookie.
 */

import { createLogger } from '@/lib/logger'

const log = createLogger('csrf')

/**
 * Pure cookie-string parser: extract the ``csrf_token`` value from a
 * raw cookie string (the value of ``document.cookie``). Exported so
 * benchmarks + unit tests can exercise the parsing path without
 * depending on DOM state.
 *
 * Returns null when the cookie is absent or its URL-encoding is
 * malformed (the CSRF interceptor then omits the header and the
 * server returns 403, which is the right failure mode).
 */
export function parseCsrfTokenFromCookieString(cookieString: string): string | null {
  const match = cookieString
    .split(';')
    .map((s) => s.trim())
    .find((row) => row.startsWith('csrf_token='))
  if (!match) return null
  const eqIdx = match.indexOf('=')
  if (eqIdx === -1) return null
  try {
    return decodeURIComponent(match.slice(eqIdx + 1))
  } catch (err) {
    // Malformed cookie encoding -- log for diagnosis, return null
    // so the CSRF interceptor omits the header (server returns 403).
    log.warn('Failed to decode csrf_token cookie:', err)
    return null
  }
}

/**
 * Read the CSRF token from the non-HttpOnly csrf_token cookie.
 *
 * Returns null when the cookie is absent (e.g. before login or after
 * cookie expiry). Thin DOM wrapper over
 * :func:`parseCsrfTokenFromCookieString`.
 */
export function getCsrfToken(): string | null {
  return parseCsrfTokenFromCookieString(document.cookie)
}
