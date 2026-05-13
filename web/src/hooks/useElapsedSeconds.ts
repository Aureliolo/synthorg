import { useEffect, useState } from 'react'

const MS_PER_SECOND = 1000

/**
 * Reactive seconds-elapsed-since-`startedAt` counter.
 *
 * Returns `null` when `startedAt` is `null`/`undefined` so callers can render
 * "Starting..." copy without a fallback elapsed value. The hook keeps a
 * `tick` counter that bumps once per second via `setInterval`; the elapsed
 * value is derived fresh on every render from `startedAt` and the current
 * wall clock, so changing `startedAt` mid-flight updates the display on
 * the very next render rather than the next tick.
 *
 * Future timestamps clamp to zero so a clock skew between client and server
 * never produces a negative elapsed display.
 *
 * For pure-formatting use, pair with `formatElapsed` from `@/utils/format`.
 */
export function useElapsedSeconds(startedAt: Date | string | null | undefined): number | null {
  // The hook does not surface ``tick`` to callers; the value only
  // exists to force a re-render once per second so the derived
  // elapsed in the return path picks up the new wall-clock time.
  // ESLint's ``@eslint-react/use-state`` rule wants destructuring,
  // but the setter-only form is the right shape here and the
  // narrow eslint-disable keeps the rule's signal everywhere else.
  // eslint-disable-next-line @eslint-react/use-state
  const tick = useState(0)
  const setTick = tick[1]

  useEffect(() => {
    if (startedAt === null || startedAt === undefined) return
    const id = setInterval(() => {
      setTick((t) => t + 1)
    }, MS_PER_SECOND)
    return () => clearInterval(id)
  }, [startedAt, setTick])

  return computeElapsedSeconds(startedAt)
}

function computeElapsedSeconds(startedAt: Date | string | null | undefined): number | null {
  if (startedAt === null || startedAt === undefined) return null
  const startedMs = startedAt instanceof Date ? startedAt.getTime() : Date.parse(startedAt)
  if (!Number.isFinite(startedMs)) return null
  const deltaMs = Date.now() - startedMs
  return Math.max(0, Math.floor(deltaMs / MS_PER_SECOND))
}
