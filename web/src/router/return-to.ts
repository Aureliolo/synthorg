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
 * Origin the candidate is resolved against.
 *
 * A constant rather than `window.location.origin`, because the decision is
 * "does this stay on whatever origin serves the dashboard", which is the same
 * decision on every deployment and in a test. A reserved TLD, so a value that
 * escapes cannot name anything reachable.
 */
const RESOLUTION_BASE = 'https://return-to.invalid'

/**
 * Read the destination out of a query string, refusing anything off-site.
 *
 * The value reaches this from a URL, so it is attacker-supplied by
 * construction: a link with `?next=https://elsewhere/` would otherwise turn
 * the login screen into an open redirect, which is the classic way a
 * credential prompt gets rehosted.
 *
 * Judged on the parse the browser will perform, never on the raw text, and the
 * PARSED path is what travels on. Deciding by string prefix reads a different
 * value from the one that eventually reaches `history.replaceState`, and the
 * gap between them is the whole vulnerability: `/..//host` clears every prefix
 * check and normalises to the scheme-relative `//host`, and the URL parser
 * strips tab, newline and carriage return before parsing, so `/%09/host` does
 * too. Resolving first collapses both families, along with percent-encoded
 * slashes, backslashes and absolute URLs, into one question with one answer.
 *
 * @param search - The login page's query string.
 * @returns A same-origin path to return to, or the dashboard.
 */
export function readReturnTo(search: string): string {
  const raw = new URLSearchParams(search).get(RETURN_TO_PARAM)
  if (raw === null || !raw.startsWith('/')) return DEFAULT_DESTINATION
  let resolved: URL
  try {
    resolved = new URL(raw, RESOLUTION_BASE)
  } catch {
    return DEFAULT_DESTINATION
  }
  if (resolved.origin !== RESOLUTION_BASE) return DEFAULT_DESTINATION
  // A path that is itself scheme-relative: what `/..//host` collapses to once
  // the dot segments are resolved, on an origin that still reads as ours.
  if (resolved.pathname.startsWith('//')) return DEFAULT_DESTINATION
  if (NOT_A_DESTINATION.has(resolved.pathname)) return DEFAULT_DESTINATION
  return resolved.pathname + resolved.search + resolved.hash
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
