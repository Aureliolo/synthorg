/** Derives every subsystem state, and the two roll-ups, from health + WS state. */

import { renderedSnapshot } from '@/stores/health'
import type {
  BackupState,
  CostRecordingState,
  MemoryHealth,
  MemoryState,
  ProviderReachability,
} from '@/api/types/system'
import type { SubsystemPhase, SubsystemReport } from '@/api/types/subsystems'
import type { HealthSnapshot, LoadState } from '@/stores/health'
import type { SubsystemState } from './health-popover.utils'

/**
 * How a declared subsystem's phase reads on the health roll-up.
 *
 * `/health` reports the infrastructure a process needs; `/subsystems` reports
 * the capabilities the reconciler has managed to install. They are different
 * questions, and reading only the first is how a status pill came to say "all
 * systems normal", and a dialog to headline "Every tracked component is
 * reporting healthy", over five subsystems the dashboard's own blockers panel
 * was listing as not up.
 *
 * `disabled` is the one phase that contributes nothing: an operator turned it
 * off, so the deployment is behaving exactly as configured. It is still listed
 * by the blockers panel, which answers "what stands between the org and
 * progress" rather than "is anything wrong", and those genuinely differ.
 */
const _SUBSYSTEM_PHASE_STATES: Record<SubsystemPhase, SubsystemState | null> = {
  active: null,
  disabled: null,
  failed: 'down',
  unreachable: 'down',
  degraded: 'degraded',
  waiting: 'degraded',
  rebuilding: 'degraded',
  blocked: 'degraded',
}

/**
 * The roll-up contribution of every declared subsystem, worst first.
 *
 * Returns an empty list when nothing has been read yet, which is the honest
 * answer: not knowing is not the same as knowing everything is fine, but it is
 * also not evidence of a fault, so it leaves the verdict to the health probe.
 */
function _subsystemPhaseStates(
  reports: readonly SubsystemReport[] | null,
): readonly SubsystemState[] {
  if (reports === null) return []
  return reports
    .map((report) => _SUBSYSTEM_PHASE_STATES[report.phase])
    .filter((state) => state !== null)
}

export interface DerivedSubsystemStates {
  readonly apiState: SubsystemState
  readonly wsState: SubsystemState
  readonly persistenceState: SubsystemState
  /**
   * Which backend is actually connected, so the card names it instead of
   * naming SQLite whatever a deployment runs. Undefined before one is.
   */
  readonly persistenceDetail: string | undefined
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
   * Whether LLM spend is currently being recorded.
   *
   * Degraded, never down, for the same reason as backups: the organisation
   * keeps working, and what it has lost is an accounting guarantee. The
   * spend still happens, so an operator wants to know the budget is
   * under-reporting before they trust a total.
   */
  readonly costRecordingState: SubsystemState
  /** What the failure means, when the backend says so. */
  readonly costRecordingDetail: string | undefined
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

/** Spend going unrecorded costs an accounting guarantee, not availability. */
const _COST_RECORDING_STATES: Record<CostRecordingState, SubsystemState> = {
  ok: 'ok',
  degraded: 'degraded',
}

/**
 * Providers report more states than a boolean can carry.
 *
 * Folded into "reachable", `degraded` renders the same green as a provider
 * failing nothing, so the one row an operator checks during a partial outage
 * is the row that hides it. `unknown` is the backend failing to read the
 * verdict at all, which must not render as an outage the operator would go
 * looking for at the provider.
 */
const _PROVIDER_STATES: Record<ProviderReachability, SubsystemState> = {
  ok: 'ok',
  degraded: 'degraded',
  down: 'down',
  unknown: 'unknown',
}

function _providersState(value: ProviderReachability | null): SubsystemState {
  return value === null ? 'unknown' : _PROVIDER_STATES[value]
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
  | 'persistenceDetail'
  | 'busState'
  | 'providersState'
  | 'memoryState'
  | 'memoryDetail'
  | 'memoryBackendState'
  | 'backupState'
  | 'backupDetail'
  | 'costRecordingState'
  | 'costRecordingDetail'
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
    persistenceDetail: health.persistence_backend ?? undefined,
    busState: _probeState(health.message_bus),
    providersState: _providersState(health.providers),
    memoryState: _MEMORY_STATES[health.memory.state],
    memoryDetail: _memoryDetailFor(health.memory),
    memoryBackendState: health.memory.state,
    backupState: _BACKUP_STATES[health.backup.state],
    backupDetail: health.backup.detail ?? undefined,
    costRecordingState: _COST_RECORDING_STATES[health.cost_recording.state],
    costRecordingDetail: health.cost_recording.detail ?? undefined,
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
    persistenceDetail: undefined,
    busState: pending,
    providersState: pending,
    memoryState: pending,
    memoryDetail: undefined,
    memoryBackendState: null,
    backupState: pending,
    backupDetail: undefined,
    costRecordingState: pending,
    costRecordingDetail: undefined,
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
  subsystems: readonly SubsystemReport[] | null = null,
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
    backend.costRecordingState,
    ..._subsystemPhaseStates(subsystems),
  ]
  return {
    ...backend,
    wsState,
    wsDetail: _wsDetailFor(wsConnected, wsReconnectExhausted, sseFallbackActive),
    withWebSocketState: _rollUpOf([...rolledUp, wsState], snapshot),
    backendOnlyState: _rollUpOf(rolledUp, snapshot),
  }
}
