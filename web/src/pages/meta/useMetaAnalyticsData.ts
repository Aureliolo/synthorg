import { useEffect, useState } from 'react'

import { getSignals, listProposals } from '@/api/endpoints/meta'
import type { ProposalSummary, SignalsResponse } from '@/api/endpoints/meta'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { getErrorMessage } from '@/utils/errors'

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
    const ctrl = { cancelled: false }
    // Defer setState writes to a microtask (per @eslint-react
    // set-state-in-effect) before kicking off the parallel fetches.
    void Promise.resolve().then(async () => {
      if (ctrl.cancelled) return
      setLoading(true)
      setSignalsError(null)
      setProposalsError(null)
      const [signalsRes, proposalsRes] = await Promise.allSettled([
        getSignals(),
        listProposals(),
      ])
      // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition -- flipped by effect cleanup during the await; CFA cannot see the closure mutation
      if (ctrl.cancelled) return
      handleSignalsResult(signalsRes, setSignals, setSignalsError)
      handleProposalsResult(proposalsRes, setProposals, setProposalsError)
      setLoading(false)
    })
    return () => {
      ctrl.cancelled = true
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
  const message = getErrorMessage(result.reason)
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
