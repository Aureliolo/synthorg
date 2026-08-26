/**
 * Where an interrupted operator was, carried across the login screen.
 *
 * A session can expire mid-run with no backend auth failure to explain it, and
 * one did about 50 minutes into a live run with a decomposition still in
 * flight: the page being watched was replaced by a login screen that looked
 * like a fresh visit and had forgotten where the operator had been.
 *
 * Carried in the URL rather than stored. It is transient navigation state, not
 * anything the org owns, and the dashboard persists no state client-side.
 */

import { ROUTES } from './routes'

/** Query parameter naming the path to return to after signing in. */
export const RETURN_TO_PARAM = 'next'

/** Where an operator lands when there is nothing better to return them to. */
const DEFAULT_DESTINATION: string = ROUTES.DASHBOARD

/**
 * The two routes a return-to must never name.
 *
 * Both are where an unauthenticated visitor already is, so returning to either
 * is a loop rather than a destination.
 */
const NOT_A_DESTINATION: ReadonlySet<string> = new Set<string>([
  ROUTES.LOGIN,
  ROUTES.SETUP,
])

/**
 * Read the destination out of a query string, refusing anything off-site.
 *
 * The value reaches this from a URL, so it is attacker-supplied by
 * construction: a link with `?next=https://elsewhere/` would otherwise turn
 * the login screen into an open redirect, which is the classic way a
 * credential prompt gets rehosted. Only a single-slash absolute path is
 * accepted, which excludes an absolute URL, a scheme-relative `//host` and a
 * backslash the browser normalises into one.
 *
 * @param search - The login page's query string.
 * @returns A same-origin path to return to, or the dashboard.
 */
export function readReturnTo(search: string): string {
  const raw = new URLSearchParams(search).get(RETURN_TO_PARAM)
  if (raw === null || !raw.startsWith('/')) return DEFAULT_DESTINATION
  if (raw.startsWith('//') || raw.startsWith('/\\')) return DEFAULT_DESTINATION
  const path = raw.split('?')[0] ?? ''
  if (NOT_A_DESTINATION.has(path)) return DEFAULT_DESTINATION
  return raw
}

/**
 * Whether a login screen was reached by being interrupted rather than visited.
 *
 * What lets the page say so. A sign-in prompt that appears unannounced in the
 * middle of a run reads as a fault in the product until it explains itself.
 *
 * @param search - The login page's query string.
 * @returns True when a destination was carried in.
 */
export function wasInterrupted(search: string): boolean {
  return new URLSearchParams(search).get(RETURN_TO_PARAM) !== null
}
