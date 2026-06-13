/** Hook deriving the four subsystem states (and overall) from health + WS state. */

import type { LoadState, SubsystemState } from './health-popover.utils'

export interface DerivedSubsystemStates {
  readonly apiState: SubsystemState
  readonly wsState: SubsystemState
  readonly persistenceState: SubsystemState
  readonly busState: SubsystemState
  readonly overallState: SubsystemState
  readonly wsDetail: string | undefined
}

function _apiStateFor(loadState: LoadState): SubsystemState {
  if (loadState.state === 'loading') return 'loading'
  if (loadState.state === 'error') return 'down'
  if (loadState.state === 'ok') return loadState.data.status === 'ok' ? 'ok' : 'down'
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

function _overallStateOf(states: readonly SubsystemState[]): SubsystemState {
  if (states.includes('down')) return 'down'
  if (states.includes('degraded')) return 'degraded'
  if (states.includes('loading')) return 'loading'
  if (states.includes('unknown')) return 'unknown'
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
  const persistenceState = _booleanProbeState(loadState, persistence)
  const busState = _booleanProbeState(loadState, messageBus)
  const overallState = _overallStateOf([apiState, wsState, persistenceState, busState])
  return { apiState, wsState, persistenceState, busState, overallState, wsDetail }
}
