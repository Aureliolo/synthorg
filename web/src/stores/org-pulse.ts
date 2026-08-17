import type { StoreApi } from 'zustand'
import { create } from 'zustand'

import { getSubsystems } from '@/api/endpoints/subsystems'
import { listTasks } from '@/api/endpoints/tasks'
import type { SubsystemReport } from '@/api/types/subsystems'
import type { Task } from '@/api/types/tasks'
import { getErrorMessage } from '@/utils/errors'

/**
 * The two reads the dashboard's pulse panel needs and nothing else already has.
 *
 * Kept out of the analytics store because both are answers to "why is the org
 * not making progress", and neither is an analytic: a subsystem phase is live
 * wiring state, and a parked task's reason is the loop's own. Read-only, so both
 * set `error` rather than raising a toast, per the store conventions.
 */
interface OrgPulseState {
  subsystems: readonly SubsystemReport[]
  /** Tasks parked at BLOCKED, which carry the reason each park waits on. */
  blockedTasks: readonly Task[]
  /**
   * Errors kept apart per read, not joined.
   *
   * The panel makes a positive claim from an empty list ("every declared
   * subsystem is up"), so it has to know WHICH input it is missing before it
   * can say anything. One collapsed message cannot answer that.
   */
  subsystemsError: string | null
  blockedTasksError: string | null
  /** True only while the first read is in flight, so a poll cannot flash it. */
  loading: boolean
  fetchOrgPulse: () => Promise<void>
  reset: () => void
}

type PulseSet = StoreApi<OrgPulseState>['setState']
type PulseGet = StoreApi<OrgPulseState>['getState']

/**
 * How many parked tasks to read.
 *
 * The panel groups them by reason and shows the counts, so it needs enough rows
 * to make the grouping honest rather than every row. A park that does not fit
 * still lands in one of the same groups.
 */
const BLOCKED_TASK_SAMPLE = 100

async function fetchOrgPulseImpl(set: PulseSet, get: PulseGet): Promise<void> {
  // Only the first read is a loading state. This is also the 30s poll, and a
  // panel that flashes "reading the org's state" every 30s reads as churn.
  const first = get().subsystems.length === 0 && get().blockedTasks.length === 0
  set({ loading: first })
  try {
    // Independently useful: a failed subsystem read must not cost the operator
    // the parked-task reasons as well, so neither is awaited inside the other.
    const [subsystemsResult, tasksResult] = await Promise.allSettled([
      getSubsystems(),
      listTasks({ status: 'blocked', limit: BLOCKED_TASK_SAMPLE }),
    ])
    set((state) => ({
      // A rejected poll keeps the last good answer rather than blanking it:
      // one transient 500 must not erase the blockers an operator is reading.
      // The error beside it is what marks the data stale.
      subsystems:
        subsystemsResult.status === 'fulfilled'
          ? subsystemsResult.value.subsystems
          : state.subsystems,
      subsystemsError:
        subsystemsResult.status === 'fulfilled'
          ? null
          : getErrorMessage(subsystemsResult.reason),
      blockedTasks:
        tasksResult.status === 'fulfilled' ? tasksResult.value.data : state.blockedTasks,
      blockedTasksError:
        tasksResult.status === 'fulfilled'
          ? null
          : getErrorMessage(tasksResult.reason),
      loading: false,
    }))
  } catch (err) {
    // Neither read settled, so both inputs are unknown.
    const message = getErrorMessage(err)
    set({ loading: false, subsystemsError: message, blockedTasksError: message })
  }
}

const INITIAL: Omit<OrgPulseState, 'fetchOrgPulse' | 'reset'> = {
  subsystems: [],
  blockedTasks: [],
  subsystemsError: null,
  blockedTasksError: null,
  loading: false,
}

export const useOrgPulseStore = create<OrgPulseState>((set, get) => ({
  ...INITIAL,
  fetchOrgPulse: () => fetchOrgPulseImpl(set, get),
  reset: () => set({ ...INITIAL }),
}))

/** Test teardown hook, registered in ``test-setup.tsx``. */
export function resetOrgPulseStore(): void {
  useOrgPulseStore.getState().reset()
}
