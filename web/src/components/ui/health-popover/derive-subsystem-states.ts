/** Hook deriving the five subsystem states (and overall) from health + WS state. */

import type { MemoryHealth, MemoryState } from '@/api/types/system'
import type { LoadState } from '@/stores/health'
import type { SubsystemState } from './health-popover.utils'

export interface DerivedSubsystemStates {
  readonly apiState: SubsystemState
  readonly wsState: SubsystemState
  readonly persistenceState: SubsystemState
  readonly busState: SubsystemState
  readonly providersState: SubsystemState
  readonly memoryState: SubsystemState
  readonly memoryDetail: string | undefined
  readonly overallState: SubsystemState
  /**
   * The same roll-up over the backend subsystems only, excluding the WebSocket.
   *
   * The status pill applies its own WebSocket priority (a disconnected stream
   * must not read as an outage before the backend has even answered once), so
   * feeding it ``overallState`` would count the WebSocket twice under two
   * different orderings and let a not-yet-connected stream report the system
   * degraded at first paint. The popover hero uses ``overallState``, because it
   * renders a WebSocket card beside the others.
   */
  readonly backendState: SubsystemState
  readonly wsDetail: string | undefined
}

/**
 * How each backend memory state reads on this surface.
 *
 * `off` is degraded, not down, and the distinction is load-bearing.
 * `unreachable` is the backend's own word for "wired but not answering", so
 * folding `off` into it would announce an outage where nothing was ever wired
 * and point an operator at a failure instead of at the embedding model they have
 * not chosen. It would also roll the whole system up as down while every other
 * component serves normally: a deployment without recall is missing a
 * capability, which is what degraded means here. The Memory card carries the
 * backend's own remedy text either way, so no specificity is lost.
 */
const _MEMORY_STATES: Record<MemoryState, SubsystemState> = {
  durable: 'ok',
  degraded: 'degraded',
  unreachable: 'down',
  off: 'degraded',
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
  // Above every softer verdict, because the probe fan-out timing out
  // answers 503 with every component null, which reads as five `unknown`
  // cards. Ranked below them this branch was unreachable on exactly the
  // outage it exists to catch. Down, not degraded: the backend already
  // decided it is not serving, and a refusal none of the cards explains
  // is the least understood outage, not the mildest.
  if (loadState.state === 'ok' && loadState.data.status !== 'ok') return 'down'
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
  const providers = loadState.state === 'ok' ? loadState.data.providers : null
  const memory = loadState.state === 'ok' ? loadState.data.memory : null
  const persistenceState = _booleanProbeState(loadState, persistence)
  const busState = _booleanProbeState(loadState, messageBus)
  const providersState = _booleanProbeState(loadState, providers)
  const memoryState = _memoryStateFor(loadState, memory)
  const memoryDetail = _memoryDetailFor(memory)
  const backendStates = [
    apiState,
    persistenceState,
    busState,
    providersState,
    memoryState,
  ]
  const overallState = _overallStateOf([...backendStates, wsState], loadState)
  const backendState = _overallStateOf(backendStates, loadState)
  return {
    apiState,
    wsState,
    persistenceState,
    busState,
    providersState,
    memoryState,
    memoryDetail,
    overallState,
    backendState,
    wsDetail,
  }
}
