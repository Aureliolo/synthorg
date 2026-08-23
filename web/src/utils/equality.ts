/** Structural equality for API payloads. */

import { isEqual } from 'es-toolkit'

/**
 * Deep structural equality over JSON-shaped values (the result of
 * parsing an API response: plain objects, arrays, primitives).
 *
 * Used by polling refreshes to skip the store write when the server
 * returned identical data: writing a fresh object every poll notifies
 * every subscriber, re-rendering charts/gauges across the app and
 * producing a ResizeObserver re-measure wave each interval.
 *
 * Wraps es-toolkit rather than calling it directly so the comparison
 * enters our code as `unknown`. The library types both parameters as
 * `any`, which would let an unchecked value spread from a call site
 * into stores that are otherwise strictly typed.
 */
export function deepEqual(a: unknown, b: unknown): boolean {
  return isEqual(a, b)
}
