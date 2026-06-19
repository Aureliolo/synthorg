import type { StoreApi } from 'zustand'
import { create } from 'zustand'

import {
  getScalingDecisions,
  getScalingSignals,
  getScalingStrategies,
  triggerScalingEvaluation,
  updateScalingPriority,
  updateScalingStrategy,
  type ScalingDecisionResponse,
  type ScalingSignalResponse,
  type ScalingStrategyResponse,
} from '@/api/endpoints/scaling'
import { createLogger } from '@/lib/logger'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import { useToastStore } from '@/stores/toast'
import { sanitizeWsString } from '@/utils/ws-sanitize'
import type { WsEvent } from '@/api/types/websocket'

const log = createLogger('scaling')

let wsRefreshInFlight = false
let wsRefreshQueued = false
let wsRefreshEpoch = 0

interface ScalingState {
  strategies: readonly ScalingStrategyResponse[]
  decisions: readonly ScalingDecisionResponse[]
  signals: readonly ScalingSignalResponse[]
  totalDecisions: number

  loading: boolean
  error: string | null
  evaluating: boolean
  mutating: boolean

  fetchAll: () => Promise<void>
  fetchStrategies: () => Promise<void>
  fetchDecisions: () => Promise<void>
  fetchSignals: () => Promise<void>
  evaluateNow: () => Promise<ScalingDecisionResponse[]>
  /** Enable/disable a strategy and refetch. Returns false on failure. */
  setStrategyEnabled: (name: string, enabled: boolean) => Promise<boolean>
  /** Persist a new strategy priority order and refetch. Returns false on failure. */
  reorderPriority: (order: readonly string[]) => Promise<boolean>
  updateFromWsEvent: (event: WsEvent) => void
  dispose: () => void
}

type ScSet = StoreApi<ScalingState>['setState']
type ScGet = StoreApi<ScalingState>['getState']

async function fetchAllImpl(set: ScSet): Promise<void> {
  set({ loading: true, error: null })
  try {
    const [strategiesR, decisionsR, signalsR] = await Promise.allSettled([
      getScalingStrategies(),
      getScalingDecisions({ limit: 50 }),
      getScalingSignals(),
    ])
    const errors = [strategiesR, decisionsR, signalsR]
      .filter((r) => r.status === 'rejected')
      .map((r) => r.reason as unknown)
    const errorMsg = errors.length > 0
      ? errors.map((e) => getErrorMessage(e)).join('; ')
      : null
    set((state) => ({
      strategies: strategiesR.status === 'fulfilled'
        ? strategiesR.value
        : state.strategies,
      decisions: decisionsR.status === 'fulfilled'
        ? decisionsR.value.data
        : state.decisions,
      totalDecisions: decisionsR.status === 'fulfilled'
        ? decisionsR.value.data.length
        : state.totalDecisions,
      signals: signalsR.status === 'fulfilled'
        ? signalsR.value
        : state.signals,
      loading: false,
      error: errorMsg,
    }))
  } catch (err) {
    log.error('Failed to fetch scaling data', err)
    set({ loading: false, error: getErrorMessage(err) })
  }
}

async function fetchDecisionsImpl(set: ScSet): Promise<void> {
  const epoch = wsRefreshEpoch
  try {
    const result = await getScalingDecisions({ limit: 50 })
    if (epoch !== wsRefreshEpoch) return
    set({ decisions: result.data, totalDecisions: result.data.length })
  } catch (err) {
    if (epoch !== wsRefreshEpoch) return
    log.error('Failed to fetch decisions', err)
    throw err
  }
}

async function fetchSignalsImpl(set: ScSet): Promise<void> {
  const epoch = wsRefreshEpoch
  try {
    const signals = await getScalingSignals()
    if (epoch !== wsRefreshEpoch) return
    set({ signals })
  } catch (err) {
    if (epoch !== wsRefreshEpoch) return
    log.error('Failed to fetch signals', err)
    throw err
  }
}

async function runWsRefresh(get: ScGet): Promise<void> {
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
    void runWsRefresh(get)
  }
}

async function setStrategyEnabledImpl(
  set: ScSet,
  get: ScGet,
  name: string,
  enabled: boolean,
): Promise<boolean> {
  set({ mutating: true })
  try {
    await updateScalingStrategy(name, enabled)
    await get().fetchStrategies()
    set({ mutating: false })
    useToastStore.getState().add({
      variant: 'success',
      title: enabled ? 'Strategy enabled' : 'Strategy disabled',
    })
    return true
  } catch (err) {
    log.error('Failed to update strategy', err)
    set({ mutating: false })
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Could not update strategy'),
      description: getErrorMessage(err),
    })
    return false
  }
}

async function reorderPriorityImpl(
  set: ScSet,
  get: ScGet,
  order: readonly string[],
): Promise<boolean> {
  set({ mutating: true })
  try {
    await updateScalingPriority(order)
    await get().fetchStrategies()
    set({ mutating: false })
    useToastStore.getState().add({ variant: 'success', title: 'Priority order updated' })
    return true
  } catch (err) {
    log.error('Failed to update priority', err)
    set({ mutating: false })
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Could not update priority'),
      description: getErrorMessage(err),
    })
    return false
  }
}

export const useScalingStore = create<ScalingState>()((set, get) => ({
  strategies: [],
  decisions: [],
  signals: [],
  totalDecisions: 0,
  loading: false,
  error: null,
  evaluating: false,
  mutating: false,

  fetchAll: () => fetchAllImpl(set),
  fetchStrategies: async () => {
    try {
      const strategies = await getScalingStrategies()
      set({ strategies })
    } catch (err) {
      log.error('Failed to fetch strategies', err)
      throw err
    }
  },
  fetchDecisions: () => fetchDecisionsImpl(set),
  fetchSignals: () => fetchSignalsImpl(set),

  evaluateNow: async () => {
    set({ evaluating: true })
    try {
      const decisions = await triggerScalingEvaluation()
      await get().fetchAll()
      set({ evaluating: false })
      return decisions
    } catch (err) {
      log.error('Failed to trigger evaluation', err)
      set({ evaluating: false, error: getErrorMessage(err) })
      throw err
    }
  },

  setStrategyEnabled: (name, enabled) => setStrategyEnabledImpl(set, get, name, enabled),

  reorderPriority: (order) => reorderPriorityImpl(set, get, order),

  updateFromWsEvent: (event: WsEvent) => {
    log.debug('Scaling WS event', sanitizeWsString(event.event_type, 128))
    void runWsRefresh(get)
  },

  dispose: () => {
    wsRefreshEpoch += 1
    wsRefreshInFlight = false
    wsRefreshQueued = false
  },
}))
