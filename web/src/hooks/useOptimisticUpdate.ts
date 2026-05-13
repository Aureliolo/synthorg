import { useCallback, useRef, useState } from 'react'
import { getErrorMessage } from '@/utils/errors'
import { createLogger } from '@/lib/logger'

const log = createLogger('useOptimisticUpdate')

/**
 * Perform an optimistic UI update with rollback on failure.
 *
 * Returns an `execute(applyOptimistic, serverAction)` function where
 * `applyOptimistic` applies the optimistic state and returns a rollback function,
 * and `serverAction` is the actual server request.
 *
 * A `null` return means one of three things:
 * - **Server error**: `error` is set with a message. The optimistic state was rolled back.
 * - **Already in-flight**: `pending` was true, so the call was a no-op.
 * - **Optimistic prepare failed**: `applyOptimistic` threw and the state was reverted.
 *
 * Callers should check `error` to distinguish failures from no-op returns.
 */
export function useOptimisticUpdate(): {
  pending: boolean
  error: string | null
  execute: <T>(
    applyOptimistic: () => () => void,
    serverAction: () => Promise<T>,
  ) => Promise<T | null>
} {
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const pendingRef = useRef(false)

  const execute = useCallback(async <T>(
    applyOptimistic: () => () => void,
    serverAction: () => Promise<T>,
  ): Promise<T | null> => {
    if (pendingRef.current) return null
    pendingRef.current = true
    setPending(true)
    setError(null)

    let rollback: () => void
    try {
      rollback = applyOptimistic()
    } catch (prepareErr) {
      pendingRef.current = false
      setPending(false)
      setError(getErrorMessage(prepareErr))
      log.error('Optimistic prepare failed:', getErrorMessage(prepareErr))
      return null
    }

    try {
      const result = await serverAction()
      return result
    } catch (err) {
      let rollbackFailed = false
      try {
        rollback()
      } catch (rollbackErr) {
        rollbackFailed = true
        log.error('Rollback failed:', getErrorMessage(rollbackErr))
      }
      const base = getErrorMessage(err)
      const msg = rollbackFailed ? `${base} (UI may be out of sync; please refresh)` : base
      setError(msg)
      log.error('Optimistic update failed:', msg)
      return null
    } finally {
      pendingRef.current = false
      setPending(false)
    }
  }, [])

  return { pending, error, execute }
}
