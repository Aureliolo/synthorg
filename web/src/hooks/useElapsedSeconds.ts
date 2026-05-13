import { useEffect, useState } from 'react'

const MS_PER_SECOND = 1000

/**
 * Reactive seconds-elapsed-since-`startedAt` counter.
 *
 * Returns `null` when `startedAt` is `null`/`undefined` so callers can render
 * "Starting..." copy without a fallback elapsed value. Updates once per
 * second via `setInterval`; the timer is torn down on unmount and when
 * `startedAt` changes (e.g. a pipeline restart). Future timestamps clamp to
 * zero so a clock skew between client and server never produces a negative
 * elapsed display.
 *
 * For pure-formatting use, pair with `formatElapsed` from `@/utils/format`.
 */
export function useElapsedSeconds(startedAt: Date | string | null | undefined): number | null {
  const initial = computeElapsedSeconds(startedAt)
  const [elapsed, setElapsed] = useState<number | null>(initial)

  useEffect(() => {
    const next = computeElapsedSeconds(startedAt)
    setElapsed(next)
    if (next === null) return
    const id = setInterval(() => {
      setElapsed(computeElapsedSeconds(startedAt))
    }, MS_PER_SECOND)
    return () => clearInterval(id)
  }, [startedAt])

  return elapsed
}

function computeElapsedSeconds(startedAt: Date | string | null | undefined): number | null {
  if (startedAt === null || startedAt === undefined) return null
  const startedMs = startedAt instanceof Date ? startedAt.getTime() : Date.parse(startedAt)
  if (!Number.isFinite(startedMs)) return null
  const deltaMs = Date.now() - startedMs
  return Math.max(0, Math.floor(deltaMs / MS_PER_SECOND))
}
