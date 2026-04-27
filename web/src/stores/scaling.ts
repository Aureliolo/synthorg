import { create } from 'zustand'

import {
  getScalingDecisions,
  getScalingSignals,
  getScalingStrategies,
  triggerScalingEvaluation,
  type ScalingDecisionResponse,
  type ScalingSignalResponse,
  type ScalingStrategyResponse,
} from '@/api/endpoints/scaling'
import { createLogger } from '@/lib/logger'
import { getErrorMessage } from '@/utils/errors'
import type { WsEvent } from '@/api/types/websocket'

const log = createLogger('scaling')

// Coalesce concurrent WS refreshes so a burst of events does not
// spawn overlapping request storms.
let wsRefreshInFlight = false
let wsRefreshQueued = false

interface ScalingState {
  // Data
  strategies: readonly ScalingStrategyResponse[]
  decisions: readonly ScalingDecisionResponse[]
  signals: readonly ScalingSignalResponse[]
  totalDecisions: number

  // UI state
  loading: boolean
  error: string | null
  evaluating: boolean

  // Actions
  fetchAll: () => Promise<void>
  fetchStrategies: () => Promise<void>
  fetchDecisions: () => Promise<void>
  fetchSignals: () => Promise<void>
  evaluateNow: () => Promise<ScalingDecisionResponse[]>
  updateFromWsEvent: (event: WsEvent) => void

  // Lifecycle (#1600 Phase 5). No-op today; future timers / listeners
  // should be torn down here so the global ``afterEach`` in
  // ``web/src/test-setup.tsx`` releases them deterministically.
  dispose: () => void
}

export const useScalingStore = create<ScalingState>()((set, get) => ({
  strategies: [],
  decisions: [],
  signals: [],
  totalDecisions: 0,
  loading: false,
  error: null,
  evaluating: false,

  fetchAll: async () => {
    set({ loading: true, error: null })
    try {
      const [strategiesR, decisionsR, signalsR] = await Promise.allSettled([
        getScalingStrategies(),
        getScalingDecisions({ limit: 50 }),
        getScalingSignals(),
      ])

      const errors = [strategiesR, decisionsR, signalsR]
        .filter((r) => r.status === 'rejected')
        .map((r) => (r as PromiseRejectedResult).reason)
      const errorMsg =
        errors.length > 0
          ? errors.map((e) => getErrorMessage(e)).join('; ')
          : null

      // Functional updater: read the latest committed state inside
      // the updater so concurrent writes that landed during our
      // request are preserved for any slice whose fetch failed.
      set((state) => ({
        strategies:
          strategiesR.status === 'fulfilled'
            ? strategiesR.value
            : state.strategies,
        decisions:
          decisionsR.status === 'fulfilled'
            ? decisionsR.value.data
            : state.decisions,
        totalDecisions:
          decisionsR.status === 'fulfilled'
            ? decisionsR.value.total ?? decisionsR.value.data.length
            : state.totalDecisions,
        signals:
          signalsR.status === 'fulfilled' ? signalsR.value : state.signals,
        loading: false,
        error: errorMsg,
      }))
    } catch (err) {
      log.error('Failed to fetch scaling data', err)
      set({ loading: false, error: getErrorMessage(err) })
    }
  },

  fetchStrategies: async () => {
    try {
      const strategies = await getScalingStrategies()
      set({ strategies })
    } catch (err) {
      log.error('Failed to fetch strategies', err)
      throw err
    }
  },

  fetchDecisions: async () => {
    try {
      const result = await getScalingDecisions({ limit: 50 })
      set({
        decisions: result.data,
        totalDecisions: result.total ?? result.data.length,
      })
    } catch (err) {
      log.error('Failed to fetch decisions', err)
      throw err
    }
  },

  fetchSignals: async () => {
    try {
      const signals = await getScalingSignals()
      set({ signals })
    } catch (err) {
      log.error('Failed to fetch signals', err)
      throw err
    }
  },

  evaluateNow: async () => {
    set({ evaluating: true })
    try {
      const decisions = await triggerScalingEvaluation()
      // Refresh all data after evaluation.
      await get().fetchAll()
      set({ evaluating: false })
      return decisions
    } catch (err) {
      log.error('Failed to trigger evaluation', err)
      set({ evaluating: false, error: getErrorMessage(err) })
      throw err
    }
  },

  updateFromWsEvent: (event: WsEvent) => {
    log.debug('Scaling WS event', event.event_type)

    const runRefresh = async (): Promise<void> => {
      if (wsRefreshInFlight) {
        wsRefreshQueued = true
        return
      }
      wsRefreshInFlight = true
      try {
        const results = await Promise.allSettled([
          get().fetchDecisions(),
          get().fetchSignals(),
        ])
        for (const r of results) {
          if (r.status === 'rejected') {
            log.error('WS event refresh partial failure', r.reason)
          }
        }
      } finally {
        wsRefreshInFlight = false
      }
      if (wsRefreshQueued) {
        wsRefreshQueued = false
        void runRefresh()
      }
    }

    void runRefresh()
  },
  dispose: () => {
    // Reserved for future teardown of timers / listeners. Today the
    // scaling store schedules no async resources; this method exists
    // so the test-setup ``afterEach`` contract is uniform across
    // domain stores (#1600 Phase 5).
  },
}))
