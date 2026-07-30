import { create } from 'zustand'
import { getHealthDetail } from '@/api/endpoints/health'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import type { HealthStatus } from '@/api/types/system'

const log = createLogger('health')

/**
 * A `/health` snapshot and how it was obtained.
 *
 * Owned here rather than in the popover package because the snapshot is what
 * the store holds and the surfaces render, so the rendering direction runs
 * component-reads-store, not store-imports-component.
 */
export type LoadState =
  | { state: 'idle' }
  | { state: 'loading' }
  | { state: 'ok', data: HealthStatus, fetchedAt: Date }
  | { state: 'error', message: string, fetchedAt: Date }

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
  /** Wall-clock used to render the snapshot's "x ago" label. */
  nowMs: number
  setNowMs: (now: number) => void
  /**
   * Refresh the snapshot. Resolves when the probe has settled and **never
   * rejects**: a failed probe is a health state (`error`) the surfaces render,
   * not an exception. Awaitable so a poller does not start a second probe while
   * the first is still in flight.
   */
  fetch: () => Promise<void>
}

/**
 * Discards a response whose probe has been superseded. Module-level rather
 * than store state because it is transport bookkeeping, not something a
 * consumer selects on, and a stale probe must lose the race even across a
 * store reset.
 */
let latestProbe = 0

export const useHealthStore = create<HealthState>()((set) => ({
  loadState: { state: 'idle' },
  nowMs: Date.now(),

  setNowMs: (now) => set({ nowMs: now }),

  fetch: async () => {
    set({ loadState: { state: 'loading' } })
    const probeId = ++latestProbe
    try {
      const data = await getHealthDetail()
      if (probeId !== latestProbe) return
      const fetchedAt = new Date()
      set({ loadState: { state: 'ok', data, fetchedAt }, nowMs: fetchedAt.getTime() })
    } catch (err: unknown) {
      if (probeId !== latestProbe) return
      const fetchedAt = new Date()
      const message = err instanceof Error ? err.message : 'Health probe failed'
      log.warn('Health probe failed', { error: sanitizeForLog(message) })
      set({ loadState: { state: 'error', message, fetchedAt }, nowMs: fetchedAt.getTime() })
    }
  },
}))

/**
 * Reset the singleton store between tests. Backend-sourced with no client
 * persistence, so the only cross-test leak is in-memory state in a shared
 * Vitest worker; the global ``afterEach`` in ``test-setup.tsx`` calls this.
 * The probe counter is bumped rather than zeroed so a request already in
 * flight from the previous test cannot land on the next test's state.
 */
export function resetHealthStore(): void {
  latestProbe += 1
  useHealthStore.setState({ loadState: { state: 'idle' }, nowMs: Date.now() })
}
