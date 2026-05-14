import { useCallback, useEffect, useRef, useState } from 'react'
import { getErrorMessage } from '@/utils/errors'
import { createLogger } from '@/lib/logger'

const log = createLogger('usePolling')

const MIN_POLL_INTERVAL = 100

/**
 * Poll a function at a fixed interval with cleanup on unmount.
 * Uses setTimeout-based scheduling to prevent overlapping async calls.
 * A run generation counter prevents stale in-flight runs from spawning
 * duplicate loops after stop/start cycles.
 */
export function usePolling(fn: () => Promise<void>, intervalMs: number): {
  active: boolean
  error: string | null
  start: () => void
  stop: () => void
} {
  const [active, setActive] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const activeRef = useRef(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const fnRef = useRef(fn)
  const runIdRef = useRef(0)
  fnRef.current = fn

  // Validate at start, not during render
  const isValidInterval = Number.isFinite(intervalMs) && intervalMs >= MIN_POLL_INTERVAL

  const scheduleTick = useCallback((runId: number) => {
    if (!activeRef.current || runId !== runIdRef.current) return
    const tick = async () => {
      if (!activeRef.current || runId !== runIdRef.current) return
      try {
        await fnRef.current()
        setError(null)
      } catch (err) {
        setError(getErrorMessage(err))
        log.error('Polling error:', err)
      }
      scheduleTick(runId)
    }
    timerRef.current = setTimeout(() => { void tick() }, intervalMs)
  }, [intervalMs])

  const start = useCallback(() => {
    if (!isValidInterval) {
      log.error(`intervalMs must be a finite number >= ${MIN_POLL_INTERVAL}, got ${intervalMs}`)
      return
    }
    if (activeRef.current) return
    activeRef.current = true
    setActive(true)
    setError(null)
    const runId = ++runIdRef.current
    const immediate = async () => {
      if (!activeRef.current || runId !== runIdRef.current) return
      try {
        await fnRef.current()
      } catch (err) {
        setError(getErrorMessage(err))
        log.error('Polling error:', err)
      }
      scheduleTick(runId)
    }
    immediate().catch((err) => {
      log.error('Polling initial run failed:', err)
    })
  }, [scheduleTick, isValidInterval, intervalMs])

  const stop = useCallback(() => {
    activeRef.current = false
    setActive(false)
    runIdRef.current++
    if (timerRef.current) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      stop()
    }
  }, [stop])

  // start/stop are stable useCallback refs; return a plain object so consumers
  // depend on the individual refs, not an object whose identity changes on every state update.
  return { active, error, start, stop }
}
