/** Hook deriving the five subsystem states (and overall) from health + WS state. */

import type { MemoryHealth, MemoryState } from '@/api/types/system'
import type { LoadState, SubsystemState } from './health-popover.utils'

export interface DerivedSubsystemStates {
  readonly apiState: SubsystemState
  readonly wsState: SubsystemState
  readonly persistenceState: SubsystemState
  readonly busState: SubsystemState
  readonly memoryState: SubsystemState
  readonly memoryDetail: string | undefined
  readonly overallState: SubsystemState
  readonly wsDetail: string | undefined
}

const _MEMORY_STATES: Record<MemoryState, SubsystemState> = {
  durable: 'ok',
  degraded: 'degraded',
  unreachable: 'down',
  off: 'down',
}

/**
 * State of the HTTP layer itself, which is what this card claims to report.
 *
 * Derived from whether the fetch succeeded, never from the readiness verdict
 * inside it: a parsed response is proof the API answered, so folding the
 * aggregate verdict in here reported a fully-serving backend as unreachable
 * whenever any one subsystem was degraded. The aggregate reaches the hero
 * through the overall roll-up, which is where it belongs.
 */
function _apiStateFor(loadState: LoadState): SubsystemState {
  if (loadState.state === 'loading') return 'loading'
  if (loadState.state === 'error') return 'down'
  if (loadState.state === 'ok') return 'ok'
  return 'unknown'
}

function _wsStateFor(
  loadState: LoadState,
  wsConnected: boolean,
  wsReconnectExhausted: boolean,
  sseFallbackActive: boolean,
): SubsystemState {
  if (wsConnected) return 'ok'
  // SSE fallback is degraded-but-live, not down: events still flow.
  if (sseFallbackActive) return 'degraded'
  if (wsReconnectExhausted) return 'down'
  if (loadState.state === 'loading') return 'loading'
  return 'degraded'
}

function _wsDetailFor(
  wsConnected: boolean,
  wsReconnectExhausted: boolean,
  sseFallbackActive: boolean,
): string | undefined {
  if (wsConnected) return undefined
  if (sseFallbackActive) return 'SSE fallback active'
  return wsReconnectExhausted ? 'reconnect budget exhausted' : 'auto-reconnecting'
}

function _booleanProbeState(loadState: LoadState, value: boolean | null | undefined): SubsystemState {
  if (loadState.state === 'loading') return 'loading'
  if (loadState.state !== 'ok') return 'unknown'
  if (value === true) return 'ok'
  if (value === false) return 'down'
  return 'unknown'
}

function _memoryStateFor(loadState: LoadState, memory: MemoryHealth | null): SubsystemState {
  if (loadState.state === 'loading') return 'loading'
  if (loadState.state !== 'ok' || memory === null) return 'unknown'
  return _MEMORY_STATES[memory.state]
}

function _memoryDetailFor(memory: MemoryHealth | null): string | undefined {
  if (memory === null) return undefined
  const backend = memory.backend.trim()
  return memory.detail ?? (backend === '' ? undefined : backend)
}

function _overallStateOf(
  states: readonly SubsystemState[],
  loadState: LoadState,
): SubsystemState {
  if (states.includes('down')) return 'down'
  if (states.includes('degraded')) return 'degraded'
  if (states.includes('loading')) return 'loading'
  if (states.includes('unknown')) return 'unknown'
  // The backend reported itself not ready for something no card covers
  // (providers, say). Degraded rather than down: it is answering.
  if (loadState.state === 'ok' && loadState.data.status !== 'ok') return 'degraded'
  return 'ok'
}

export function deriveHealthSubsystemStates(
  loadState: LoadState,
  wsConnected: boolean,
  wsReconnectExhausted: boolean,
  sseFallbackActive: boolean,
): DerivedSubsystemStates {
  const apiState = _apiStateFor(loadState)
  const wsState = _wsStateFor(loadState, wsConnected, wsReconnectExhausted, sseFallbackActive)
  const wsDetail = _wsDetailFor(wsConnected, wsReconnectExhausted, sseFallbackActive)
  const persistence = loadState.state === 'ok' ? loadState.data.persistence : null
  const messageBus = loadState.state === 'ok' ? loadState.data.message_bus : null
  const memory = loadState.state === 'ok' ? loadState.data.memory : null
  const persistenceState = _booleanProbeState(loadState, persistence)
  const busState = _booleanProbeState(loadState, messageBus)
  const memoryState = _memoryStateFor(loadState, memory)
  const memoryDetail = _memoryDetailFor(memory)
  const overallState = _overallStateOf(
    [apiState, wsState, persistenceState, busState, memoryState],
    loadState,
  )
  return {
    apiState,
    wsState,
    persistenceState,
    busState,
    memoryState,
    memoryDetail,
    overallState,
    wsDetail,
  }
}
