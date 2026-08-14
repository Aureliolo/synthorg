/**
 * Reads every active agent's own dispatch record for the side-by-side
 * comparison. Live from the REST API on every mount; nothing is persisted
 * client-side (Pure API Consumer).
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { listDispatchProfiles } from '@/api/endpoints/agents'
import type { DispatchProfile } from '@/api/types/agents'
import { createLogger } from '@/lib/logger'
import { createCancellationToken, type CancellationToken } from '@/utils/cancellation'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'

const log = createLogger('useDispatchProfiles')

export interface DispatchProfilesState {
  rows: readonly DispatchProfile[]
  loading: boolean
  error: string | null
}

export interface DispatchProfilesController {
  state: DispatchProfilesState
  load: () => void
}

export function useDispatchProfiles(): DispatchProfilesController {
  const [state, setState] = useState<DispatchProfilesState>({
    rows: [],
    loading: true,
    error: null,
  })

  const tokenRef = useRef<CancellationToken | null>(null)

  // Every state write is fenced behind the token that started its request.
  // Without it a Retry can resolve before the request it replaced, and the
  // older response overwrites the newer one: the user clicks Retry, the
  // fetch succeeds, and the screen reverts to the error it just cleared.
  const loadWith = useCallback((token: CancellationToken) => {
    setState((prev) => ({ ...prev, loading: true, error: null }))
    void listDispatchProfiles()
      .then((rows) => {
        if (token.cancelled()) return
        setState({ rows, loading: false, error: null })
      })
      .catch((err: unknown) => {
        if (token.cancelled()) return
        const message = getErrorMessage(err)
        log.error('load dispatch profiles failed', { error: sanitizeForLog(message) })
        setState({ rows: [], loading: false, error: message })
      })
  }, [])

  const load = useCallback(() => {
    tokenRef.current?.cancel()
    const token = createCancellationToken()
    tokenRef.current = token
    loadWith(token)
  }, [loadWith])

  useEffect(() => {
    // Minted synchronously so cleanup cancels the request THIS effect
    // started, and cleanup cancels whichever token is active so a manual
    // reload is not left running past unmount.
    const token = createCancellationToken()
    tokenRef.current = token
    void Promise.resolve().then(() => {
      if (token.cancelled()) return
      loadWith(token)
    })
    return () => {
      tokenRef.current?.cancel()
    }
  }, [loadWith])

  return { state, load }
}
