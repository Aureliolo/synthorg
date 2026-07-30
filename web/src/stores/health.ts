import { create } from 'zustand'
import { getHealthDetail } from '@/api/endpoints/health'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import type { HealthStatus } from '@/api/types/system'

const log = createLogger('health')

/** A `/health` response and when it arrived. */
export interface HealthSnapshot {
  readonly data: HealthStatus;
  readonly fetchedAt: Date;
}

/**
 * A `/health` snapshot and how it was obtained.
 *
 * Owned here rather than in the popover package because the snapshot is what
 * the store holds and the surfaces render, so the rendering direction runs
 * component-reads-store, not store-imports-component.
 *
 * `loading` carries the snapshot it is refreshing over, so a periodic re-probe
 * has something to keep showing. Without it every poll tick and every dialog
 * open would wipe a good snapshot back to "checking...", and the surfaces would
 * have no way to express "this is a moment old and I am re-asking" at all.
 */
export type LoadState =
  | { state: 'idle' }
  | { state: 'loading'; previous: HealthSnapshot | null }
  | { state: 'ok'; data: HealthStatus; fetchedAt: Date }
  | { state: 'error'; message: string; fetchedAt: Date }

/**
 * The snapshot a surface should render for this load state, if any.
 *
 * One resolution point rather than a `state === 'ok'` check per subsystem, so a
 * refresh cannot half-apply: either every card reads the carried-over snapshot
 * or none of them do.
 *
 * @returns The settled snapshot, or `null` when nothing has settled yet.
 */
export function renderedSnapshot(loadState: LoadState): HealthSnapshot | null {
  if (loadState.state === 'ok') {
    return { data: loadState.data, fetchedAt: loadState.fetchedAt }
  }
  if (loadState.state === 'loading') return loadState.previous
  return null
}

/**
 * The one `/health` snapshot every operator-facing health surface reads.
 *
 * The status pill and the health dialog answer the same question, so they read
 * the same snapshot and cannot disagree; it also means the dialog's refresh
 * button refreshes what the pill shows.
 *
 * Deliberately `/health` and not `/readyz`. Readiness is a binary supervisor
 * gate carrying no component topology, and a subsystem may abstain from its
 * verdict on purpose (an unwired memory backend does, rather than take a
 * serving deployment offline), so it cannot answer an operator's question about
 * which subsystem needs attention. `getReadiness` keeps its own job for
 * supervisors and load balancers.
 */
interface HealthState {
  loadState: LoadState
  /**
   * Refresh the snapshot. Resolves when the probe has settled and **never
   * rejects**: a failed probe is a health state (`error`) the surfaces render,
   * not an exception.
   *
   * Awaiting it serialises one poller's own ticks. It does not prevent an
   * independent caller (a dialog opening, a refresh button) from overlapping;
   * `latestProbe` is what makes an overlap safe.
   */
  fetchHealth: () => Promise<void>
  /**
   * Release an open probe and stop waiting on it, falling back to the last
   * settled snapshot (or `idle` if there is none).
   *
   * For a surface that is going away: stopping the poller only stops the next
   * tick, so without this the tick already in flight keeps a request open for
   * the client's whole timeout on behalf of a component that no longer exists.
   */
  cancelProbe: () => void
}

/**
 * Discards a response whose probe has been superseded.
 *
 * Module-level, not store state: it is transport bookkeeping no consumer
 * selects on, and `resetHealthStore` has to be able to invalidate an in-flight
 * probe from outside the store. Matches `planForecast`'s `requestToken`.
 * Monotonic, so a probe that took its id before a reset can never match again.
 */
let latestProbe = 0

/**
 * Releases the superseded probe's transport rather than only ignoring its
 * result. `latestProbe` alone leaves the socket open for the client's full
 * timeout, which outlives the component that asked and which the test suite's
 * active-handle gate reads as a leak.
 *
 * Every abort here is paired with a bump of `latestProbe`, so an aborted probe's
 * rejection is always dropped by the guard and never surfaces as an `error`
 * state.
 */
let inFlight: AbortController | null = null

/** Invalidate and release whatever probe is currently open. */
function supersedeInFlight(): void {
  latestProbe += 1
  inFlight?.abort()
  inFlight = null
}

export const useHealthStore = create<HealthState>()((set, get) => ({
  loadState: { state: 'idle' },

  fetchHealth: async () => {
    // Claimed before announcing `loading` so the announcement is ordered
    // against concurrent probes rather than racing them, and so the carried
    // snapshot is the one current at claim time.
    supersedeInFlight()
    const probeId = latestProbe
    const controller = new AbortController()
    inFlight = controller
    set({
      loadState: { state: 'loading', previous: renderedSnapshot(get().loadState) },
    })
    try {
      const data = await getHealthDetail(controller.signal)
      if (probeId !== latestProbe) return
      inFlight = null
      set({ loadState: { state: 'ok', data, fetchedAt: new Date() } })
    } catch (err: unknown) {
      if (probeId !== latestProbe) return
      inFlight = null
      const message = err instanceof Error ? err.message : 'Health probe failed'
      log.warn('Health probe failed', { error: sanitizeForLog(message) })
      set({ loadState: { state: 'error', message, fetchedAt: new Date() } })
    }
  },

  cancelProbe: () => {
    supersedeInFlight()
    const { loadState } = get()
    if (loadState.state !== 'loading') return
    // A `loading` nobody will settle would leave the surfaces saying
    // "checking..." forever, so the carried snapshot becomes current again
    // under its own original timestamp rather than a freshly stamped one.
    set({
      loadState:
        loadState.previous === null
          ? { state: 'idle' }
          : { state: 'ok', ...loadState.previous },
    })
  },
}))

/**
 * Reset the singleton store between tests. Backend-sourced with no client
 * persistence, so the only cross-test leak is in-memory state in a shared
 * Vitest worker; the global `afterEach` in `test-setup.tsx` calls this.
 * The probe counter is bumped rather than zeroed so a request already in
 * flight from the previous test cannot land on the next test's state, and the
 * open request is aborted so it does not survive as a handle into the next one.
 */
export function resetHealthStore(): void {
  supersedeInFlight()
  useHealthStore.setState({ loadState: { state: 'idle' } })
}
