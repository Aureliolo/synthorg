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
  /**
   * Read the subsystem reports alone, for the always-mounted health pill.
   *
   * Separate from {@link fetchOrgPulse} because the pill needs the subsystem
   * verdict and nothing else; the blocked-task sample is the dashboard
   * panel's input.
   */
  fetchSubsystems: () => Promise<void>
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

/**
 * Which subsystem read owns the answer.
 *
 * Two callers read subsystems, on different cadences and from different
 * mounts: the dashboard panel's 30-second poll and the always-mounted health
 * pill. Both write the same two fields, and nothing ordered them, so a slow
 * response could land after a faster later one and put a stale verdict back
 * on screen. The pill is the surface that shows on EVERY route, so the stale
 * answer it restores is the one an operator sees next.
 *
 * A counter rather than an abort: neither read is cancellable at the point
 * the other starts, and the question is not "stop that request" but "is this
 * result still the newest", which only the write site can answer.
 */
let subsystemRevision = 0

function claimSubsystemRead(): number {
  subsystemRevision += 1
  return subsystemRevision
}

async function fetchOrgPulseImpl(set: PulseSet, get: PulseGet): Promise<void> {
  // Only the first read is a loading state. This is also the 30s poll, and a
  // panel that flashes "reading the org's state" every 30s reads as churn.
  const first = !get().loaded
  const revision = claimSubsystemRead()
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
    // The blocked-task half is this reader's alone, so it always applies; the
    // subsystem half is shared with the pill and applies only while newest.
    const current = revision === subsystemRevision
    set((state) => ({
      // A failed poll keeps the last good answer rather than blanking it: one
      // transient 500 must not erase the blockers an operator is reading. The
      // error beside it is what marks the data stale.
      subsystems: current ? reports.items ?? state.subsystems : state.subsystems,
      subsystemsError: current ? reports.error : state.subsystemsError,
      blockedTasks: parked.items ?? state.blockedTasks,
      blockedTasksError: parked.error,
      loading: false,
      loaded: true,
    }))
  } catch (err) {
    // Neither read settled, so both inputs are unknown. Still loaded: the panel
    // now has an answer, and "we tried and here is why not" is one.
    const message = getErrorMessage(err)
    const current = revision === subsystemRevision
    set((state) => ({
      loading: false,
      loaded: true,
      subsystemsError: current ? message : state.subsystemsError,
      blockedTasksError: message,
    }))
  }
}

async function fetchSubsystemsImpl(set: PulseSet): Promise<void> {
  // The subsystem half alone, for the always-mounted health pill.
  //
  // The pill folds the subsystem verdict into its roll-up but fetched
  // nothing, so it only ever saw what some OTHER page had put in this store.
  // On the dashboard that meant "system degraded" with five subsystems
  // blocked, and on every other route the same deployment read "all systems
  // normal" seconds later. Falling short of the whole truth is what the pill
  // was designed to do; saying the strongest possible thing on no data is
  // not falling short, it is contradicting the panel it opens.
  //
  // Not `fetchOrgPulse`, which also samples blocked tasks: that is the
  // dashboard panel's input and the pill has no use for it, so putting it on
  // every page would be a query added rather than a verdict fixed.
  const revision = claimSubsystemRead()
  try {
    const response = await getSubsystems()
    const reports = settle<SubsystemReport>(
      { status: 'fulfilled', value: response },
      (value: SubsystemsResponse) => value.subsystems,
    )
    if (revision !== subsystemRevision) return
    set((state) => ({
      // A failed poll keeps the last good answer, as the full pulse read
      // does: one transient 500 must not blank the verdict.
      subsystems: reports.items ?? state.subsystems,
      subsystemsError: reports.error,
    }))
  } catch (err) {
    if (revision !== subsystemRevision) return
    set({ subsystemsError: getErrorMessage(err) })
  }
}

const INITIAL: Omit<OrgPulseState, 'fetchOrgPulse' | 'fetchSubsystems' | 'reset'> = {
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
  fetchSubsystems: () => fetchSubsystemsImpl(set),
  reset: () => {
    // The revision counter is module state, so it outlives the store's own
    // fields and would carry one test's in-flight reads into the next.
    subsystemRevision = 0
    set({ ...INITIAL })
  },
}))

/** Test teardown hook, registered in ``test-setup.tsx``. */
export function resetOrgPulseStore(): void {
  useOrgPulseStore.getState().reset()
}
