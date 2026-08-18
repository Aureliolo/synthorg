import type { StoreApi } from 'zustand'
import { create } from 'zustand'

import { getSubsystems } from '@/api/endpoints/subsystems'
import { listTasks } from '@/api/endpoints/tasks'
import type { PaginatedResult } from '@/api/client'
import type { SubsystemReport, SubsystemsResponse } from '@/api/types/subsystems'
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
  /**
   * Whether a read has ever settled.
   *
   * Tracked rather than inferred from the lists, because an all-clear org
   * answers with two empty lists every time and would otherwise look like a
   * store that had never read anything: the panel would re-enter its loading
   * state on all thirty-second polls, forever, exactly when nothing is wrong.
   */
  loaded: boolean
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

/**
 * What a read that resolved but did not carry a list is reported as.
 *
 * A rejected promise is not the only way a read fails. An envelope shaped
 * differently from the contract resolves perfectly well and hands `undefined`
 * to a field the panel's derivation iterates, and that derivation runs in the
 * hook, ABOVE the panel's own error boundary, so the whole dashboard page goes
 * down rather than the one half that could not be read.
 */
const NOT_A_LIST = 'the response did not carry the expected list'

/** One read's outcome: its list, or the reason there is none. */
interface PulseRead<T> {
  readonly items: readonly T[] | null
  readonly error: string | null
}

/**
 * Settle one read into a list or a reason, never into a silent empty list.
 *
 * The list is checked for at runtime rather than trusted from the declared
 * type: that type is a claim about the backend, not about the bytes that
 * arrived, which is why `pick` answers `unknown`.
 *
 * Returns:
 *   The list when the read both resolved and carried one, else the reason.
 */
function settle<T>(
  result: PromiseSettledResult<unknown>,
  pick: (value: never) => unknown,
): PulseRead<T> {
  if (result.status !== 'fulfilled') {
    return { items: null, error: getErrorMessage(result.reason) }
  }
  const picked = pick(result.value as never)
  return Array.isArray(picked)
    ? { items: picked as readonly T[], error: null }
    : { items: null, error: NOT_A_LIST }
}

async function fetchOrgPulseImpl(set: PulseSet, get: PulseGet): Promise<void> {
  // Only the first read is a loading state. This is also the 30s poll, and a
  // panel that flashes "reading the org's state" every 30s reads as churn.
  const first = !get().loaded
  set({ loading: first })
  try {
    // Independently useful: a failed subsystem read must not cost the operator
    // the parked-task reasons as well, so neither is awaited inside the other.
    const [subsystemsResult, tasksResult] = await Promise.allSettled([
      getSubsystems(),
      listTasks({ status: 'blocked', limit: BLOCKED_TASK_SAMPLE }),
    ])
    const reports = settle<SubsystemReport>(
      subsystemsResult,
      (value: SubsystemsResponse) => value.subsystems,
    )
    const parked = settle<Task>(tasksResult, (value: PaginatedResult<Task>) => value.data)
    set((state) => ({
      // A failed poll keeps the last good answer rather than blanking it: one
      // transient 500 must not erase the blockers an operator is reading. The
      // error beside it is what marks the data stale.
      subsystems: reports.items ?? state.subsystems,
      subsystemsError: reports.error,
      blockedTasks: parked.items ?? state.blockedTasks,
      blockedTasksError: parked.error,
      loading: false,
      loaded: true,
    }))
  } catch (err) {
    // Neither read settled, so both inputs are unknown. Still loaded: the panel
    // now has an answer, and "we tried and here is why not" is one.
    const message = getErrorMessage(err)
    set({
      loading: false,
      loaded: true,
      subsystemsError: message,
      blockedTasksError: message,
    })
  }
}

const INITIAL: Omit<OrgPulseState, 'fetchOrgPulse' | 'reset'> = {
  subsystems: [],
  blockedTasks: [],
  subsystemsError: null,
  blockedTasksError: null,
  loading: false,
  loaded: false,
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
