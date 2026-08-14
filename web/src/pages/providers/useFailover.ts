/**
 * Reads what an operator declared, and every time it had to be used. Live
 * from the REST API on every mount; nothing is persisted client-side (Pure
 * API Consumer).
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { getFailoverDeclaration, listFailoverEvents } from '@/api/endpoints/providers'
import type { FailoverDeclaration, ProviderFailoverEvent } from '@/api/types/providers'
import { createLogger } from '@/lib/logger'
import { createCancellationToken, type CancellationToken } from '@/utils/cancellation'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'

const log = createLogger('useFailover')

const EMPTY_DECLARATION: FailoverDeclaration = { enabled: false, routes: [] }

export interface FailoverState {
  declaration: FailoverDeclaration
  events: readonly ProviderFailoverEvent[]
  loading: boolean
  error: string | null
}

export interface FailoverController {
  state: FailoverState
  load: () => void
}

/** Stable row key: the id is assigned per engagement and never reused. */
export function failoverEventKey(event: ProviderFailoverEvent): string {
  return event.id
}

/**
 * @param declaredProvider - Scope events to one declared connection, or
 *   report every engagement when omitted.
 */
export function useFailover(declaredProvider?: string): FailoverController {
  const [state, setState] = useState<FailoverState>({
    declaration: EMPTY_DECLARATION,
    events: [],
    loading: true,
    error: null,
  })

  const tokenRef = useRef<CancellationToken | null>(null)

  const loadWith = useCallback(
    (token: CancellationToken) => {
      setState((prev) => ({ ...prev, loading: true, error: null }))
      const events =
        declaredProvider === undefined
          ? listFailoverEvents()
          : listFailoverEvents({ declaredProvider })
      void Promise.all([getFailoverDeclaration(), events])
        .then(([declaration, page]) => {
          if (token.cancelled()) return
          setState({ declaration, events: page.data, loading: false, error: null })
        })
        .catch((err: unknown) => {
          if (token.cancelled()) return
          const message = getErrorMessage(err)
          log.error('load failover failed', { error: sanitizeForLog(message) })
          setState({
            declaration: EMPTY_DECLARATION,
            events: [],
            loading: false,
            error: message,
          })
        })
    },
    [declaredProvider],
  )

  const load = useCallback(() => {
    tokenRef.current?.cancel()
    const token = createCancellationToken()
    tokenRef.current = token
    loadWith(token)
  }, [loadWith])

  useEffect(() => {
    // Minted synchronously so the cleanup cancels this effect's own
    // request; created inside the deferred callback it would outlive a
    // cleanup that ran first, and a stale provider's events would land
    // over the current one's.
    const token = createCancellationToken()
    tokenRef.current = token
    void Promise.resolve().then(() => {
      if (token.cancelled()) return
      loadWith(token)
    })
    return () => {
      token.cancel()
    }
  }, [loadWith])

  return { state, load }
}
