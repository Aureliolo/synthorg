/** Structural equality for API payloads. */

/**
 * Deep structural equality over JSON-shaped values (the result of
 * parsing an API response: plain objects, arrays, primitives).
 *
 * Used by polling refreshes to skip the store write when the server
 * returned identical data: writing a fresh object every poll notifies
 * every subscriber, re-rendering charts/gauges across the app and
 * producing a ResizeObserver re-measure wave each interval.
 */
export function deepEqual(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true
  if (typeof a !== 'object' || typeof b !== 'object' || a === null || b === null) {
    return false
  }
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false
    return a.every((item, i) => deepEqual(item, b[i]))
  }
  const aRecord = a as Record<string, unknown>
  const bRecord = b as Record<string, unknown>
  const aKeys = Object.keys(aRecord)
  if (aKeys.length !== Object.keys(bRecord).length) return false
  return aKeys.every(
    (key) => Object.hasOwn(bRecord, key) && deepEqual(aRecord[key], bRecord[key]),
  )
}
