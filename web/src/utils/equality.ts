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
 * `any`, and `any` parameters are what collapse a generic's inferred
 * type when the function is passed as a value into a typed slot (an
 * equality-fn option, say). `unknown` parameters are contravariant and
 * do not.
 */
export function deepEqual(a: unknown, b: unknown): boolean {
  return isEqual(a, b)
}
