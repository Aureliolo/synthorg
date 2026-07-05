import { useEffect, useState } from 'react'

import { getSignals, listProposals } from '@/api/endpoints/meta'
import type { ProposalSummary, SignalsResponse } from '@/api/endpoints/meta'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { getErrorMessage, unavailableMessage } from '@/utils/errors'
import { SIGNALS_UNAVAILABLE_MESSAGE } from '@/stores/meta'
import { createCancellationToken } from '@/utils/cancellation'

const log = createLogger('MetaAnalyticsPage')

export interface MetaAnalyticsData {
  signals: SignalsResponse | null
  proposals: readonly ProposalSummary[]
  loading: boolean
  signalsError: string | null
  proposalsError: string | null
}

export function useMetaAnalyticsData(): MetaAnalyticsData {
  const [signals, setSignals] = useState<SignalsResponse | null>(null)
  const [proposals, setProposals] = useState<readonly ProposalSummary[]>([])
  const [loading, setLoading] = useState(true)
  // Per-resource error state so the operator sees which fetch failed (not a
  // conflated "x; y" string). When both are non-null the page is fully
  // unavailable; when one is null the page renders the available data plus a
  // partial-failure banner pointing at the failed resource.
  const [signalsError, setSignalsError] = useState<string | null>(null)
  const [proposalsError, setProposalsError] = useState<string | null>(null)

  useEffect(() => {
    const token = createCancellationToken()
    // Defer setState writes to a microtask (per @eslint-react
    // set-state-in-effect) before kicking off the parallel fetches.
    void Promise.resolve().then(async () => {
      if (token.cancelled()) return
      setLoading(true)
      setSignalsError(null)
      setProposalsError(null)
      const [signalsRes, proposalsRes] = await Promise.allSettled([
        getSignals(),
        listProposals(),
      ])
      if (token.cancelled()) return
      handleSignalsResult(signalsRes, setSignals, setSignalsError)
      handleProposalsResult(proposalsRes, setProposals, setProposalsError)
      setLoading(false)
    })
    return () => {
      token.cancel()
    }
  }, [])

  return { signals, proposals, loading, signalsError, proposalsError }
}

function handleSignalsResult(
  result: PromiseSettledResult<SignalsResponse>,
  setSignals: (v: SignalsResponse | null) => void,
  setError: (err: string | null) => void,
): void {
  if (result.status === 'fulfilled') {
    setSignals(result.value)
    return
  }
  // A fail-closed 503 (signals disabled for this deployment) gets the same
  // specific guidance the meta store renders, not the generic fetch-error
  // copy; any other failure falls through to the generic message.
  const message = unavailableMessage(result.reason, SIGNALS_UNAVAILABLE_MESSAGE)
  log.error('getSignals failed', { error: sanitizeForLog(message) })
  setError(message)
}

function handleProposalsResult(
  result: PromiseSettledResult<readonly ProposalSummary[]>,
  setProposals: (v: readonly ProposalSummary[]) => void,
  setError: (err: string | null) => void,
): void {
  if (result.status === 'fulfilled') {
    setProposals(result.value)
    return
  }
  const message = getErrorMessage(result.reason)
  log.error('listProposals failed', { error: sanitizeForLog(message) })
  setError(message)
}
