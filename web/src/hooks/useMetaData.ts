import { useEffect, useRef } from 'react'

import type {
  AbTestRecord,
  EvolutionAxisStat,
  EvolutionSummary,
  MetaConfig,
  ProposalSummary,
  SignalsResponse,
} from '@/api/endpoints/meta'
import { useMetaStore } from '@/stores/meta'

const POLL_INTERVAL = 30_000

interface UseMetaDataReturn {
  config: MetaConfig | null
  proposals: readonly ProposalSummary[]
  abTests: readonly AbTestRecord[]
  evolutionSummary: EvolutionSummary | null
  evolutionAxes: readonly EvolutionAxisStat[]
  signals: SignalsResponse | null
  loading: boolean
  error: string | null
}

export function useMetaData(): UseMetaDataReturn {
  const config = useMetaStore((s) => s.config)
  const proposals = useMetaStore((s) => s.proposals)
  const abTests = useMetaStore((s) => s.abTests)
  const evolutionSummary = useMetaStore((s) => s.evolutionSummary)
  const evolutionAxes = useMetaStore((s) => s.evolutionAxes)
  const signals = useMetaStore((s) => s.signals)
  const loading = useMetaStore((s) => s.loading)
  const error = useMetaStore((s) => s.error)
  const fetchAll = useMetaStore((s) => s.fetchAll)

  const fetchRef = useRef(fetchAll)
  fetchRef.current = fetchAll

  // Initial fetch on mount.
  useEffect(() => {
    void fetchRef.current()
  }, [])

  // Polling -- skip tick while a request is in-flight to avoid
  // overlapping fetches when the tab resumes from background.
  const loadingRef = useRef(loading)
  loadingRef.current = loading

  useEffect(() => {
    const id = setInterval(() => {
      if (!loadingRef.current) {
        void fetchRef.current()
      }
    }, POLL_INTERVAL)
    return () => clearInterval(id)
  }, [])

  return {
    config,
    proposals,
    abTests,
    evolutionSummary,
    evolutionAxes,
    signals,
    loading,
    error,
  }
}
