import { create, type StoreApi } from 'zustand'

import {
  getEvolutionAxisStats,
  getEvolutionSummary,
  getMetaConfig,
  getSignals,
  listABTests,
  listProposals,
  listRecentAlerts,
  type AbTestRecord,
  type AlertSummary,
  type EvolutionAxisStat,
  type EvolutionSummary,
  type MetaConfig,
  type ProposalSummary,
  type SignalsResponse,
} from '@/api/endpoints/meta'
import { createLogger } from '@/lib/logger'
import { getErrorMessage, unavailableMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'

const log = createLogger('meta')

type MetaSet = StoreApi<MetaState>['setState']

/**
 * Fail-closed 503 copy for a signals fetch, shared with the meta-analytics
 * page (`pages/meta/useMetaAnalyticsData.ts`) so both surfaces render the
 * same "signals not enabled for this deployment" guidance.
 */
export const SIGNALS_UNAVAILABLE_MESSAGE =
  'Signal reporting is not enabled for this deployment. Ask your administrator to enable it.'

type FetchAllResults = readonly [
  PromiseSettledResult<Awaited<ReturnType<typeof getMetaConfig>>>,
  PromiseSettledResult<Awaited<ReturnType<typeof listProposals>>>,
  PromiseSettledResult<Awaited<ReturnType<typeof listABTests>>>,
  PromiseSettledResult<Awaited<ReturnType<typeof getSignals>>>,
  PromiseSettledResult<Awaited<ReturnType<typeof getEvolutionSummary>>>,
  PromiseSettledResult<Awaited<ReturnType<typeof getEvolutionAxisStats>>>,
]

// Map the settled results to a partial update carrying ONLY the fields that
// resolved, so a failed endpoint leaves its prior value untouched instead of
// being wiped. Extracted so runFetchAll stays under the complexity cap.
function buildFetchAllUpdate(results: FetchAllResults): Partial<MetaState> {
  const [config, proposals, abTests, signals, evolutionSummary, evolutionAxes] = results
  const update: Partial<MetaState> = {}
  if (config.status === 'fulfilled') update.config = config.value
  if (proposals.status === 'fulfilled') update.proposals = proposals.value
  if (abTests.status === 'fulfilled') update.abTests = abTests.value
  if (signals.status === 'fulfilled') update.signals = signals.value
  if (evolutionSummary.status === 'fulfilled') {
    update.evolutionSummary = evolutionSummary.value
  }
  if (evolutionAxes.status === 'fulfilled') {
    update.evolutionAxes = evolutionAxes.value
  }
  return update
}

async function runFetchAll(set: MetaSet): Promise<void> {
  set({ loading: true, error: null })
  // allSettled (not all): a single failing endpoint must not wipe the data
  // the other five returned. Only successfully-fetched fields are updated;
  // failed ones keep their prior value and the first error is surfaced.
  const results = await Promise.allSettled([
    getMetaConfig(),
    listProposals(),
    listABTests(),
    getSignals(),
    getEvolutionSummary(),
    getEvolutionAxisStats(),
  ])
  const failure = results.find(
    (r): r is PromiseRejectedResult => r.status === 'rejected',
  )
  if (failure) {
    log.error('Failed to fetch some meta data', sanitizeForLog(failure.reason))
  }
  set({
    ...buildFetchAllUpdate(results),
    error: failure ? getErrorMessage(failure.reason) : null,
    loading: false,
  })
}

async function runFetchConfig(set: MetaSet): Promise<void> {
  set({ error: null })
  try {
    set({ config: await getMetaConfig() })
  } catch (err) {
    log.error('Failed to fetch meta config', sanitizeForLog(err))
    set({ error: getErrorMessage(err) })
  }
}

async function runFetchProposals(set: MetaSet): Promise<void> {
  set({ error: null })
  try {
    set({ proposals: await listProposals() })
  } catch (err) {
    log.error('Failed to fetch proposals', sanitizeForLog(err))
    set({ error: getErrorMessage(err) })
  }
}

async function runFetchAlerts(set: MetaSet): Promise<void> {
  set({ error: null })
  try {
    // A reasonably-sized recent set (bounded single page, not paginateAll)
    // is enough for the surfaces that list recent alerts.
    set({ alerts: await listRecentAlerts() })
  } catch (err) {
    log.error('Failed to fetch alerts', sanitizeForLog(err))
    set({ error: getErrorMessage(err) })
  }
}

async function runFetchSignals(set: MetaSet): Promise<void> {
  set({ error: null })
  try {
    set({ signals: await getSignals() })
  } catch (err) {
    log.error('Failed to fetch signals', sanitizeForLog(err))
    set({ error: unavailableMessage(err, SIGNALS_UNAVAILABLE_MESSAGE) })
  }
}

interface MetaState {
  // Data
  config: MetaConfig | null
  proposals: readonly ProposalSummary[]
  alerts: readonly AlertSummary[]
  abTests: readonly AbTestRecord[]
  evolutionSummary: EvolutionSummary | null
  evolutionAxes: readonly EvolutionAxisStat[]
  signals: SignalsResponse | null

  // UI state
  loading: boolean
  error: string | null

  // Actions
  fetchAll: () => Promise<void>
  /** Light config-only fetch for surfaces that gate on flags alone. */
  fetchConfig: () => Promise<void>
  fetchProposals: () => Promise<void>
  fetchAlerts: () => Promise<void>
  fetchSignals: () => Promise<void>
}

export const useMetaStore = create<MetaState>((set) => ({
  config: null,
  proposals: [],
  alerts: [],
  abTests: [],
  evolutionSummary: null,
  evolutionAxes: [],
  signals: null,
  loading: false,
  error: null,

  fetchAll: () => runFetchAll(set),
  fetchConfig: () => runFetchConfig(set),
  fetchProposals: () => runFetchProposals(set),
  fetchAlerts: () => runFetchAlerts(set),
  fetchSignals: () => runFetchSignals(set),
}))
