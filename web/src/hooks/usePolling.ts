import { useCallback, useEffect, useRef, useState } from 'react'
import { getErrorMessage } from '@/utils/errors'
import { createLogger } from '@/lib/logger'

const log = createLogger('usePolling')

const MIN_POLL_INTERVAL = 100

export interface UsePollingOptions {
  /**
   * Optional gate evaluated immediately before each scheduled tick.
   * Returning ``true`` causes the tick to be skipped (the poll
   * function does not run) and a new timer is armed for the next
   * interval. Use for "we already have fresh data from a different
   * source" branches (typically WebSocket push freshness) so the
   * polling backoff does not double-charge the API for state the UI
   * already holds.
   */
  skipIfFresh?: () => boolean
}

export interface UsePollingReturn {
  active: boolean
  error: string | null
  /**
   * True while a poll-function invocation is in flight. Pages surface
   * this as a quiet "refreshing" indicator on the header so users see
   * the system is up to date without the heavyweight initial loader.
   */
  isRefetching: boolean
  start: () => void
  stop: () => void
}

/**
 * Poll a function at a fixed interval with cleanup on unmount.
 * Uses setTimeout-based scheduling to prevent overlapping async calls.
 * A run generation counter prevents stale in-flight runs from spawning
 * duplicate loops after stop/start cycles.
 *
 * Background-tab discipline: when ``document.hidden === true`` the
 * scheduled tick skips the poll and reschedules instead, so a tab
 * left open in the background does not burn battery / API budget.
 * A ``visibilitychange`` listener re-arms a near-immediate tick when
 * the tab becomes visible again so the user sees fresh data on
 * return.
 */
export function usePolling(
  fn: () => Promise<void>,
  intervalMs: number,
  options: UsePollingOptions = {},
): UsePollingReturn {
  const [active, setActive] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [isRefetching, setIsRefetching] = useState(false)
  const activeRef = useRef(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const fnRef = useRef(fn)
  const skipIfFreshRef = useRef(options.skipIfFresh)
  const runIdRef = useRef(0)
  fnRef.current = fn
  skipIfFreshRef.current = options.skipIfFresh

  // Validate at start, not during render
  const isValidInterval = Number.isFinite(intervalMs) && intervalMs >= MIN_POLL_INTERVAL

  const scheduleTick = useCallback((runId: number, delayMs: number = intervalMs) => {
    if (!activeRef.current || runId !== runIdRef.current) return
    const tick = async () => {
      if (!activeRef.current || runId !== runIdRef.current) return
      // Hidden tab: skip the poll and reschedule. Cheaper than
      // running fetch -> render -> dropping the result.
      if (typeof document !== 'undefined' && document.hidden) {
        scheduleTick(runId)
        return
      }
      // WS-driven freshness: skip when the caller's recent-update
      // gate says we already have fresh state.
      if (skipIfFreshRef.current?.()) {
        scheduleTick(runId)
        return
      }
      setIsRefetching(true)
      try {
        await fnRef.current()
        setError(null)
      } catch (err) {
        setError(getErrorMessage(err))
        log.error('Polling error:', err)
      } finally {
        setIsRefetching(false)
      }
      scheduleTick(runId)
    }
    timerRef.current = setTimeout(() => { void tick() }, delayMs)
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
      // Mirror the visibility / freshness gates from the scheduled
      // tick path so the initial-mount poll obeys the same rules.
      if (typeof document !== 'undefined' && document.hidden) {
        scheduleTick(runId)
        return
      }
      if (skipIfFreshRef.current?.()) {
        scheduleTick(runId)
        return
      }
      setIsRefetching(true)
      try {
        await fnRef.current()
      } catch (err) {
        setError(getErrorMessage(err))
        log.error('Polling error:', err)
      } finally {
        setIsRefetching(false)
      }
      scheduleTick(runId)
    }
    immediate().catch((err) => {
      // Defensive: the inner try/catch above already converts a
      // rejected ``fn()`` into a setError + log; this catch only
      // fires if scheduleTick itself rejects, which should never
      // happen. Belt-and-braces so an unexpected throw cannot leak.
      // Guard against a stale start/stop cycle: if the run generation
      // has already moved on, the error belongs to a discarded run
      // and must not clobber the active state.
      if (!activeRef.current || runId !== runIdRef.current) return
      setError(getErrorMessage(err))
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

  // Visibilitychange resume: when the tab becomes visible, kick a
  // near-immediate refresh so the user sees fresh data without
  // waiting up to ``intervalMs`` for the next scheduled tick.
  useEffect(() => {
    if (typeof document === 'undefined') return
    const handler = () => {
      if (document.hidden) return
      if (!activeRef.current) return
      // Cancel the in-flight scheduled timer and arm a 0ms tick on
      // the SAME run generation. Bumping the generation would race
      // any in-progress await; we instead let the current tick run
      // through but pre-empt the wait.
      if (timerRef.current) {
        clearTimeout(timerRef.current)
        timerRef.current = null
      }
      scheduleTick(runIdRef.current, 0)
    }
    document.addEventListener('visibilitychange', handler)
    return () => {
      document.removeEventListener('visibilitychange', handler)
    }
  }, [scheduleTick])

  // start/stop are stable useCallback refs; return a plain object so consumers
  // depend on the individual refs, not an object whose identity changes on every state update.
  return { active, error, isRefetching, start, stop }
}
