import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'
import { getErrorMessage } from '@/utils/errors'
import { createLogger } from '@/lib/logger'

const log = createLogger('usePolling')

const MIN_POLL_INTERVAL = 100

export interface UsePollingOptions {
  /**
   * Optional gate evaluated immediately before each scheduled tick.
   * Returning `true` causes the tick to be skipped (the poll
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

interface PollRefs {
  readonly activeRef: RefObject<boolean>
  readonly timerRef: RefObject<ReturnType<typeof setTimeout> | null>
  readonly fnRef: RefObject<() => Promise<void>>
  readonly skipIfFreshRef: RefObject<(() => boolean) | undefined>
  readonly runIdRef: RefObject<number>
  readonly inFlightRef: RefObject<boolean>
  readonly pendingResumeRef: RefObject<boolean>
}

interface PollHandlers {
  readonly setError: (e: string | null) => void
  readonly setIsRefetching: (v: boolean) => void
}

/**
 * Wraps the caller-supplied freshness gate so a throw from the user's
 * callback cannot bubble past the scheduling code and leave the timer
 * un-armed. Returning false on error means "treat as not fresh":
 * we err on the side of polling so we never silently freeze the loop.
 */
function _shouldSkipIfFresh(
  refs: PollRefs,
  setError: (e: string | null) => void,
): boolean {
  const gate = refs.skipIfFreshRef.current
  if (!gate) return false
  try {
    return Boolean(gate())
  } catch (err) {
    setError(getErrorMessage(err))
    log.error('Polling freshness gate threw:', err)
    return false
  }
}

/** Cheap pre-flight: same run generation + tab visible + freshness gate not set. */
function _shouldRunPoll(
  refs: PollRefs,
  runId: number,
  handlers: PollHandlers,
): boolean {
  if (!refs.activeRef.current || runId !== refs.runIdRef.current) return false
  if (typeof document !== 'undefined' && document.hidden) return false
  if (_shouldSkipIfFresh(refs, handlers.setError)) return false
  return true
}

/**
 * Invoke `fnRef.current()` exactly once, observing the in-flight flag
 * and the isRefetching state. Errors surface as a setError + log.error;
 * the helper never throws.
 */
async function _invokePoll(refs: PollRefs, handlers: PollHandlers): Promise<void> {
  refs.inFlightRef.current = true
  handlers.setIsRefetching(true)
  try {
    await refs.fnRef.current()
    handlers.setError(null)
  } catch (err) {
    handlers.setError(getErrorMessage(err))
    log.error('Polling error:', err)
  } finally {
    refs.inFlightRef.current = false
    handlers.setIsRefetching(false)
  }
}

/**
 * Allocate all of the polling refs as a single bundle. Hoisting the
 * useRef calls into a sub-hook keeps `usePolling`'s body under the
 * function-length cap; the rule-of-hooks contract is preserved because
 * the helper is always called at the top level of `usePolling`.
 */
function usePollRefs(
  fn: () => Promise<void>,
  options: UsePollingOptions,
): PollRefs {
  const activeRef = useRef(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const fnRef = useRef(fn)
  const skipIfFreshRef = useRef(options.skipIfFresh)
  const runIdRef = useRef(0)
  const inFlightRef = useRef(false)
  const pendingResumeRef = useRef(false)
  fnRef.current = fn
  skipIfFreshRef.current = options.skipIfFresh
  return {
    activeRef, timerRef, fnRef, skipIfFreshRef,
    runIdRef, inFlightRef, pendingResumeRef,
  }
}

/** Create the visibilitychange handler used by the resume effect. */
function _buildVisibilityHandler(
  refs: PollRefs,
  scheduleTick: (runId: number, delay?: number) => void,
): () => void {
  return () => {
    if (document.hidden) return
    if (!refs.activeRef.current) return
    // Defer the resume tick if a poll-function call is currently
    // awaiting; scheduling a new 0ms tick alongside an in-flight call
    // would let two `fn()` invocations interleave and produce out-of-
    // order store writes. The currently-running tick consumes
    // `pendingResumeRef` on completion.
    if (refs.inFlightRef.current) {
      refs.pendingResumeRef.current = true
      return
    }
    // No poll in flight: cancel any armed scheduled tick and arm a 0ms
    // tick on the same run generation so the resume runs immediately.
    if (refs.timerRef.current) {
      clearTimeout(refs.timerRef.current)
      refs.timerRef.current = null
    }
    scheduleTick(refs.runIdRef.current, 0)
  }
}

/**
 * Poll a function at a fixed interval with cleanup on unmount.
 * Uses setTimeout-based scheduling to prevent overlapping async calls.
 * A run generation counter prevents stale in-flight runs from spawning
 * duplicate loops after stop/start cycles.
 *
 * Background-tab discipline: when `document.hidden === true` the
 * scheduled tick skips the poll and reschedules instead, so a tab
 * left open in the background does not burn battery / API budget.
 * A `visibilitychange` listener re-arms a near-immediate tick when
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
  const refs = usePollRefs(fn, options)
  const handlers: PollHandlers = { setError, setIsRefetching }
  const { activeRef, timerRef, runIdRef, pendingResumeRef } = refs
  const isValidInterval = Number.isFinite(intervalMs) && intervalMs >= MIN_POLL_INTERVAL

  const scheduleTick = useCallback(
    (runId: number, delayMs: number = intervalMs) => {
      if (!activeRef.current || runId !== runIdRef.current) return
      const tick = async (): Promise<void> => {
        if (!_shouldRunPoll(refs, runId, handlers)) {
          if (activeRef.current && runId === runIdRef.current) scheduleTick(runId)
          return
        }
        await _invokePoll(refs, handlers)
        // Visibility resume deferred a tick because we were in-flight:
        // honour it now with a 0ms reschedule, then clear the flag so
        // the catch-up only fires once per pending resume.
        if (pendingResumeRef.current) {
          pendingResumeRef.current = false
          scheduleTick(runId, 0)
          return
        }
        scheduleTick(runId)
      }
      timerRef.current = setTimeout(() => { void tick() }, delayMs)
    },
    // eslint-disable-next-line @eslint-react/exhaustive-deps -- refs / handlers are derived from refs that don't trigger re-render
    [intervalMs],
  )

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
    void (async () => {
      if (_shouldRunPoll(refs, runId, handlers)) await _invokePoll(refs, handlers)
      scheduleTick(runId)
    })()
    // eslint-disable-next-line @eslint-react/exhaustive-deps -- refs / handlers are derived from refs that don't trigger re-render
  }, [scheduleTick, isValidInterval, intervalMs])

  const stop = useCallback(() => {
    activeRef.current = false
    setActive(false)
    runIdRef.current++
    pendingResumeRef.current = false
    if (timerRef.current) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
    // eslint-disable-next-line @eslint-react/exhaustive-deps -- ref handles are stable identities; the rule lists them because they were destructured from a non-ref local
  }, [])

  useEffect(() => stop, [stop])

  useEffect(() => {
    if (typeof document === 'undefined') return
    const handler = _buildVisibilityHandler(refs, scheduleTick)
    document.addEventListener('visibilitychange', handler)
    return () => document.removeEventListener('visibilitychange', handler)
    // eslint-disable-next-line @eslint-react/exhaustive-deps -- refs is derived from refs that don't trigger re-render
  }, [scheduleTick])

  return { active, error, isRefetching, start, stop }
}
