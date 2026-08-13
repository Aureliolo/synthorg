/**
 * Reads what an operator declared, and every time it had to be used. Live
 * from the REST API on every mount; nothing is persisted client-side (Pure
 * API Consumer).
 */
import { useCallback, useEffect, useState } from 'react'
import { getFailoverDeclaration, listFailoverEvents } from '@/api/endpoints/providers'
import type { FailoverDeclaration, ProviderFailoverEvent } from '@/api/types/providers'
import { createLogger } from '@/lib/logger'
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

  const load = useCallback(() => {
    setState((prev) => ({ ...prev, loading: true, error: null }))
    const events =
      declaredProvider === undefined
        ? listFailoverEvents()
        : listFailoverEvents({ declaredProvider })
    void Promise.all([getFailoverDeclaration(), events])
      .then(([declaration, page]) => {
        setState({ declaration, events: page.data, loading: false, error: null })
      })
      .catch((err: unknown) => {
        const message = getErrorMessage(err)
        log.error('load failover failed', { error: sanitizeForLog(message) })
        setState({
          declaration: EMPTY_DECLARATION,
          events: [],
          loading: false,
          error: message,
        })
      })
  }, [declaredProvider])

  useEffect(() => {
    void Promise.resolve().then(() => {
      load()
    })
  }, [load])

  return { state, load }
}
