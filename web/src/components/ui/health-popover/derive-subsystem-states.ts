/** Derives every subsystem state, and the two roll-ups, from health + WS state. */

import { renderedSnapshot } from '@/stores/health'
import type { BackupState, MemoryHealth, MemoryState } from '@/api/types/system'
import type { HealthSnapshot, LoadState } from '@/stores/health'
import type { SubsystemState } from './health-popover.utils'

export interface DerivedSubsystemStates {
  readonly apiState: SubsystemState
  readonly wsState: SubsystemState
  readonly persistenceState: SubsystemState
  readonly busState: SubsystemState
  readonly providersState: SubsystemState
  readonly memoryState: SubsystemState
  readonly memoryDetail: string | undefined
  /**
   * The backend's own memory state, carried beside the surface mapping.
   *
   * `memoryState` collapses `off` into `degraded` on purpose, but the two part
   * company on what the operator does next: `off` means no embedding model was
   * ever chosen, which is theirs to fix, while every other unhealthy state is a
   * wired backend misbehaving, where naming the embedder would misdirect. Null
   * until a snapshot settles.
   */
  readonly memoryBackendState: MemoryState | null
  /**
   * Whether a backup service is wired for this boot.
   *
   * Absent reads `degraded`, never `down`: the deployment serves correctly and
   * has lost a recovery capability, which is exactly why the backend keeps it
   * out of the readiness verdict too.
   */
  readonly backupState: SubsystemState
  /** Why there is no coverage, when the backend says so. */
  readonly backupDetail: string | undefined
  /**
   * Roll-up across every subsystem including the WebSocket.
   *
   * For the dialog hero, which renders a WebSocket card beside the others and so
   * wants one verdict covering all of them.
   */
  readonly withWebSocketState: SubsystemState
  /**
   * The same roll-up over the backend subsystems only, excluding the WebSocket.
   *
   * For the status pill, which applies its own WebSocket priority: a
   * disconnected stream must not read as an outage before the backend has
   * answered once. Handing it the WebSocket-inclusive roll-up would count the
   * stream twice under two different orderings and report the system degraded at
   * first paint.
   *
   * Neither field is the default choice; both are named for what they fold in,
   * because picking the wrong one is silent and produces a plausible-looking
   * wrong verdict rather than an error.
   */
  readonly backendOnlyState: SubsystemState
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

function _probeState(value: boolean | null): SubsystemState {
  if (value === true) return 'ok'
  if (value === false) return 'down'
  return 'unknown'
}

/**
 * Backups absent is degraded, not down, for the same reason memory `off` is.
 *
 * A process with no backup coverage serves every request correctly; what it has
 * lost is a recovery capability. The backend keeps this out of its readiness
 * roll-up so a supervisor cannot restart a healthy deployment over it, and
 * reading it as `down` here would announce an outage the operator can see is
 * not happening. `unattempted` means backups were never tried for this boot,
 * which is not a verdict at all.
 */
const _BACKUP_STATES: Record<BackupState, SubsystemState> = {
  wired: 'ok',
  absent: 'degraded',
  unattempted: 'unknown',
}

function _memoryDetailFor(memory: MemoryHealth): string | undefined {
  const backend = memory.backend.trim()
  return memory.detail ?? (backend === '' ? undefined : backend)
}

/** The backend subsystems, which are the ones a `/health` snapshot reports. */
type BackendStates = Pick<
  DerivedSubsystemStates,
  | 'apiState'
  | 'persistenceState'
  | 'busState'
  | 'providersState'
  | 'memoryState'
  | 'memoryDetail'
  | 'memoryBackendState'
  | 'backupState'
  | 'backupDetail'
>

/**
 * Every subsystem's state from a snapshot that has settled.
 *
 * `apiState` is `ok` by construction rather than derived from the readiness
 * verdict inside the body: a parsed response is proof the API answered, so
 * folding the aggregate verdict in here reported a fully-serving backend as
 * unreachable whenever any one subsystem was degraded. The aggregate reaches
 * the hero through the roll-up, which is where it belongs. It also stays `ok`
 * through a refresh over an already-settled snapshot, so the pill does not blink
 * through "checking..." on every poll tick.
 */
function _settledStates(snapshot: HealthSnapshot): BackendStates {
  const health = snapshot.data
  return {
    apiState: 'ok',
    persistenceState: _probeState(health.persistence),
    busState: _probeState(health.message_bus),
    providersState: _probeState(health.providers),
    memoryState: _MEMORY_STATES[health.memory.state],
    memoryDetail: _memoryDetailFor(health.memory),
    memoryBackendState: health.memory.state,
    backupState: _BACKUP_STATES[health.backup.state],
    backupDetail: health.backup.detail ?? undefined,
  }
}

/**
 * Every subsystem's state, whether or not anything has settled.
 *
 * The no-snapshot case is resolved once here rather than re-checked per
 * component: with no body there is nothing component-specific to say, and five
 * copies of the same check are five chances for one of them to disagree.
 */
function _backendStatesOf(
  loadState: LoadState,
  snapshot: HealthSnapshot | null,
): BackendStates {
  if (snapshot !== null) return _settledStates(snapshot)
  const pending: SubsystemState = loadState.state === 'loading' ? 'loading' : 'unknown'
  return {
    // Only the API card can say anything without a body, and a failed probe is
    // exactly what it reports.
    apiState: loadState.state === 'error' ? 'down' : pending,
    persistenceState: pending,
    busState: pending,
    providersState: pending,
    memoryState: pending,
    memoryDetail: undefined,
    memoryBackendState: null,
    backupState: pending,
    backupDetail: undefined,
  }
}

function _rollUpOf(
  states: readonly SubsystemState[],
  snapshot: HealthSnapshot | null,
): SubsystemState {
  if (states.includes('down')) return 'down'
  // Above every softer verdict, because the probe fan-out timing out
  // answers 503 with every component null, which reads as five `unknown`
  // cards. Ranked below them this branch was unreachable on exactly the
  // outage it exists to catch. Down, not degraded: the backend already
  // decided it is not serving, and a refusal none of the cards explains
  // is the least understood outage, not the mildest.
  if (snapshot !== null && snapshot.data.status !== 'ok') return 'down'
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
  const snapshot = renderedSnapshot(loadState)
  const backend = _backendStatesOf(loadState, snapshot)
  const wsState = _wsStateFor(loadState, wsConnected, wsReconnectExhausted, sseFallbackActive)
  const rolledUp = [
    backend.apiState,
    backend.persistenceState,
    backend.busState,
    backend.providersState,
    backend.memoryState,
    backend.backupState,
  ]
  return {
    ...backend,
    wsState,
    wsDetail: _wsDetailFor(wsConnected, wsReconnectExhausted, sseFallbackActive),
    withWebSocketState: _rollUpOf([...rolledUp, wsState], snapshot),
    backendOnlyState: _rollUpOf(rolledUp, snapshot),
  }
}
