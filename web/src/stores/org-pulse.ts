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
  loading: boolean
  error: string | null
  fetchOrgPulse: () => Promise<void>
  reset: () => void
}

type PulseSet = StoreApi<OrgPulseState>['setState']

/**
 * How many parked tasks to read.
 *
 * The panel groups them by reason and shows the counts, so it needs enough rows
 * to make the grouping honest rather than every row. A park that does not fit
 * still lands in one of the same groups.
 */
const BLOCKED_TASK_SAMPLE = 100

async function fetchOrgPulseImpl(set: PulseSet): Promise<void> {
  set({ loading: true, error: null })
  try {
    // Independently useful: a failed subsystem read must not cost the operator
    // the parked-task reasons as well, so neither is awaited inside the other.
    const [subsystemsResult, tasksResult] = await Promise.allSettled([
      getSubsystems(),
      listTasks({ status: 'blocked', limit: BLOCKED_TASK_SAMPLE }),
    ])
    const rejected = [subsystemsResult, tasksResult].filter(
      (result): result is PromiseRejectedResult => result.status === 'rejected',
    )
    set({
      subsystems:
        subsystemsResult.status === 'fulfilled'
          ? subsystemsResult.value.subsystems
          : [],
      blockedTasks: tasksResult.status === 'fulfilled' ? tasksResult.value.data : [],
      loading: false,
      error: rejected.length === 0 ? null : getErrorMessage(rejected[0]!.reason),
    })
  } catch (err) {
    set({ loading: false, error: getErrorMessage(err) })
  }
}

const INITIAL: Omit<OrgPulseState, 'fetchOrgPulse' | 'reset'> = {
  subsystems: [],
  blockedTasks: [],
  loading: false,
  error: null,
}

export const useOrgPulseStore = create<OrgPulseState>((set) => ({
  ...INITIAL,
  fetchOrgPulse: () => fetchOrgPulseImpl(set),
  reset: () => set({ ...INITIAL }),
}))

/** Test teardown hook, registered in ``test-setup.tsx``. */
export function resetOrgPulseStore(): void {
  useOrgPulseStore.getState().reset()
}
