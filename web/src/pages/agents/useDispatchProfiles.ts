/**
 * Reads every active agent's own dispatch record for the side-by-side
 * comparison. Live from the REST API on every mount; nothing is persisted
 * client-side (Pure API Consumer).
 */
import { useCallback, useEffect, useState } from 'react'
import { listDispatchProfiles } from '@/api/endpoints/agents'
import type { DispatchProfile } from '@/api/types/agents'
import { createLogger } from '@/lib/logger'
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

  const load = useCallback(() => {
    setState((prev) => ({ ...prev, loading: true, error: null }))
    void listDispatchProfiles()
      .then((rows) => {
        setState({ rows, loading: false, error: null })
      })
      .catch((err: unknown) => {
        const message = getErrorMessage(err)
        log.error('load dispatch profiles failed', { error: sanitizeForLog(message) })
        setState({ rows: [], loading: false, error: message })
      })
  }, [])

  useEffect(() => {
    void Promise.resolve().then(() => {
      load()
    })
  }, [load])

  return { state, load }
}
