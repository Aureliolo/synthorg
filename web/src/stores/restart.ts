import { create } from 'zustand'
import { getLiveness } from '@/api/endpoints/health'
import { restartBackend } from '@/api/endpoints/restart'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'

const log = createLogger('restart')

/** How often to ask whether the replacement process is answering yet. */
const POLL_INTERVAL_MS = 1000

/**
 * When to stop waiting and hand the operator back control.
 *
 * A restart that has not answered by now is not a slow restart, it is one that
 * did not come back, and leaving a spinner up forever hides that. The message
 * on timeout says so rather than implying it is still coming.
 */
const POLL_TIMEOUT_MS = 60_000

export interface RestartState {
  /** True from the moment the backend accepts until it answers again. */
  restarting: boolean
  restart: () => Promise<boolean>
}

/**
 * Timers and abort handles for an in-flight wait.
 *
 * Module-level rather than store state: no consumer selects on them, and
 * `resetRestartStore` has to be able to cancel a wait from outside the store,
 * which the test suite's active-handle gate requires.
 */
let pollTimer: ReturnType<typeof setTimeout> | null = null
let deadlineTimer: ReturnType<typeof setTimeout> | null = null

function clearTimers(): void {
  if (pollTimer !== null) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
  if (deadlineTimer !== null) {
    clearTimeout(deadlineTimer)
    deadlineTimer = null
  }
}

/**
 * Resolve once the replacement process answers, or false on timeout.
 *
 * Liveness rather than readiness: the question is whether a process is
 * serving at all, and readiness stays false through the boot the operator is
 * waiting on, so waiting for it would report a healthy restart as a failure.
 */
function waitForBackend(): Promise<boolean> {
  return new Promise<boolean>((resolve) => {
    let settled = false
    const finish = (ok: boolean) => {
      if (settled) return
      settled = true
      clearTimers()
      resolve(ok)
    }
    deadlineTimer = setTimeout(() => finish(false), POLL_TIMEOUT_MS)
    const poll = () => {
      void getLiveness()
        .then(() => finish(true))
        .catch(() => {
          // Expected while the old process is going away and the new one has
          // not bound its port: a failed probe is the normal middle of a
          // restart, not an error to report.
          if (!settled) pollTimer = setTimeout(poll, POLL_INTERVAL_MS)
        })
    }
    pollTimer = setTimeout(poll, POLL_INTERVAL_MS)
  })
}

export const useRestartStore = create<RestartState>((set) => ({
  restarting: false,

  restart: async () => {
    set({ restarting: true })
    try {
      const accepted = await restartBackend()
      // The backend answers before signalling itself, so waiting out its own
      // stated delay keeps the first probe from hitting the process that is
      // about to exit and reading it as "already back".
      await new Promise((resolve) =>
        setTimeout(resolve, accepted.delay_seconds * 1000),
      )
      const back = await waitForBackend()
      set({ restarting: false })
      if (!back) {
        useToastStore.getState().add({
          variant: 'error',
          title: 'Backend did not come back',
          description:
            'The restart was accepted but nothing answered within a minute. ' +
            'Check the deployment before saving further settings.',
        })
        return false
      }
      // A full reload rather than a refetch: the settings that needed a
      // restart are the ones read at boot, and the SPA holds state derived
      // from the old process's answers throughout.
      window.location.reload()
      return true
    } catch (err) {
      clearTimers()
      set({ restarting: false })
      log.error('restart failed:', getErrorMessage(err))
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Could not restart the backend'),
        description: getErrorMessage(err),
      })
      return false
    }
  },
}))

/**
 * Drop any in-flight wait and its timers.
 *
 * Registered in `test-setup.tsx`: a pending poll would otherwise outlive its
 * test as a real timer handle.
 */
export function resetRestartStore(): void {
  clearTimers()
  useRestartStore.setState({ restarting: false })
}
