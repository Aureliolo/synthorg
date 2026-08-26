import { http, HttpResponse } from 'msw'
import { apiError, emptyPageEnvelope, successFor } from '@/mocks/handlers'
import type { getSubsystems } from '@/api/endpoints/subsystems'
import type { Task } from '@/api/types/tasks'
import { useOrgPulseStore } from '@/stores/org-pulse'
import { server } from '@/test-setup'

/**
 * The panel makes a positive claim out of an empty list ("every declared
 * subsystem is up"), so this store has to tell "nothing is wrong" apart from
 * "the read failed", per input, and must not blank a good answer when one
 * poll rejects.
 */

function subsystemsHandler(phase: 'active' | 'blocked' = 'active') {
  return http.get('/api/v1/subsystems', () =>
    HttpResponse.json(
      successFor<typeof getSubsystems>({
        subsystems: [
          { name: 'memory_backend', phase, waiting_on: [], detail: null },
        ],
        active: phase === 'active' ? 1 : 0,
        degraded: 0,
        waiting: 0,
        unreachable: 0,
        rebuilding: 0,
        blocked: phase === 'blocked' ? 1 : 0,
        failed: 0,
        disabled: 0,
      }),
    ),
  )
}

/**
 * A subsystems read held open until the test lets it answer.
 *
 * The hazard needs one read still in flight across a reset AND a later read,
 * which no instant handler can produce.
 *
 * `onStart` fires as the handler is entered, and the test awaits it before
 * touching the handler set. MSW resolves a request against the handlers
 * current when it dispatches, which is a microtask after `fetch` returns:
 * without the signal, a `server.use` on the next line can land first, the
 * "held" request is served by the NEW handler, and the assertion passes
 * having exercised nothing.
 */
function heldSubsystems(gate: Promise<void>, onStart: () => void) {
  return http.get('/api/v1/subsystems', async () => {
    onStart()
    await gate
    return HttpResponse.json(
      successFor<typeof getSubsystems>({
        subsystems: [
          { name: 'memory_backend', phase: 'blocked', waiting_on: [], detail: null },
        ],
        active: 0,
        degraded: 0,
        waiting: 0,
        unreachable: 0,
        rebuilding: 0,
        blocked: 1,
        failed: 0,
        disabled: 0,
      }),
    )
  })
}

function failingSubsystems() {
  return http.get('/api/v1/subsystems', () =>
    HttpResponse.json(apiError('subsystem read failed'), { status: 500 }),
  )
}

function failingTasks() {
  return http.get('/api/v1/tasks', () =>
    HttpResponse.json(apiError('task read failed'), { status: 500 }),
  )
}

function emptyTasks() {
  return http.get('/api/v1/tasks', () =>
    HttpResponse.json(emptyPageEnvelope<Task>()),
  )
}

/**
 * A 200 whose payload is shaped nothing like the contract.
 *
 * Not hypothetical: an older backend without the route, or a proxy answering
 * in its own words, resolves the promise and hands `undefined` where the
 * panel's derivation iterates a list.
 */
function misshapedSubsystems() {
  return http.get('/api/v1/subsystems', () =>
    HttpResponse.json({ data: [], error: null, error_detail: null, success: true }),
  )
}

describe('useOrgPulseStore', () => {
  it('records both reads when both succeed', async () => {
    server.use(subsystemsHandler('blocked'), emptyTasks())

    await useOrgPulseStore.getState().fetchOrgPulse()

    const state = useOrgPulseStore.getState()
    expect(state.subsystems).toHaveLength(1)
    expect(state.subsystemsError).toBeNull()
    expect(state.blockedTasksError).toBeNull()
    expect(state.loading).toBe(false)
  })

  it('keeps the halves apart when only one read fails', async () => {
    server.use(failingSubsystems(), emptyTasks())

    await useOrgPulseStore.getState().fetchOrgPulse()

    const state = useOrgPulseStore.getState()
    // The half that answered is trusted; the half that did not is marked, so
    // the panel cannot claim an all-clear built out of an error.
    expect(state.subsystemsError).not.toBeNull()
    expect(state.blockedTasksError).toBeNull()
  })

  it('treats a 200 carrying no list as a failed read, not as an all-clear', async () => {
    server.use(misshapedSubsystems(), emptyTasks())

    await useOrgPulseStore.getState().fetchOrgPulse()

    const state = useOrgPulseStore.getState()
    // The list stays a list. Handing `undefined` to the panel's derivation
    // throws in the hook, above the panel's own error boundary, which takes
    // the whole dashboard page down rather than this one half.
    expect(state.subsystems).toEqual([])
    expect(state.subsystemsError).not.toBeNull()
    expect(state.blockedTasksError).toBeNull()
  })

  it('marks both halves when both reads fail', async () => {
    server.use(failingSubsystems(), failingTasks())

    await useOrgPulseStore.getState().fetchOrgPulse()

    const state = useOrgPulseStore.getState()
    expect(state.subsystemsError).not.toBeNull()
    expect(state.blockedTasksError).not.toBeNull()
  })

  it('keeps the last good answer when a later poll rejects', async () => {
    server.use(subsystemsHandler('blocked'), emptyTasks())
    await useOrgPulseStore.getState().fetchOrgPulse()
    const good = useOrgPulseStore.getState().subsystems

    server.use(failingSubsystems())
    await useOrgPulseStore.getState().fetchOrgPulse()

    const state = useOrgPulseStore.getState()
    // Blanking it would erase the blockers the operator was reading, and read
    // as "nothing is wrong" rather than "we could not look".
    expect(state.subsystems).toEqual(good)
    expect(state.subsystemsError).not.toBeNull()
  })

  it('is loading only on the first read', async () => {
    server.use(subsystemsHandler('blocked'), emptyTasks())
    const seen: boolean[] = []
    const unsubscribe = useOrgPulseStore.subscribe((state) => {
      seen.push(state.loading)
    })

    await useOrgPulseStore.getState().fetchOrgPulse()
    const afterFirst = seen.includes(true)
    seen.length = 0
    await useOrgPulseStore.getState().fetchOrgPulse()
    unsubscribe()

    expect(afterFirst).toBe(true)
    // A 30s poll that flashes "reading the org's state" every tick reads as
    // churn, so the second pass never raises the flag.
    expect(seen).not.toContain(true)
  })

  it('stays out of loading on a poll that finds an all-clear org', async () => {
    // The healthy answer is two empty lists, every time. Inferring "first read"
    // from emptiness makes every 30s poll look like the first, so the panel of
    // a perfectly healthy org flashes its loading state forever.
    server.use(subsystemsHandler('active'), emptyTasks())
    await useOrgPulseStore.getState().fetchOrgPulse()
    expect(useOrgPulseStore.getState().subsystems).toHaveLength(1)
    expect(useOrgPulseStore.getState().blockedTasks).toEqual([])

    const seen: boolean[] = []
    const unsubscribe = useOrgPulseStore.subscribe((state) => {
      seen.push(state.loading)
    })
    await useOrgPulseStore.getState().fetchOrgPulse()
    unsubscribe()

    expect(seen).not.toContain(true)
  })

  it('counts a wholly failed read as having read, so the next poll is quiet', async () => {
    server.use(failingSubsystems(), failingTasks())
    await useOrgPulseStore.getState().fetchOrgPulse()
    expect(useOrgPulseStore.getState().loaded).toBe(true)
  })

  it('clears every field on reset', async () => {
    server.use(subsystemsHandler('blocked'), failingTasks())
    await useOrgPulseStore.getState().fetchOrgPulse()

    useOrgPulseStore.getState().reset()

    const state = useOrgPulseStore.getState()
    expect(state.subsystems).toEqual([])
    expect(state.blockedTasks).toEqual([])
    expect(state.subsystemsError).toBeNull()
    expect(state.blockedTasksError).toBeNull()
    expect(state.loading).toBe(false)
    // Reset means never read, so the next fetch is a first read again.
    expect(state.loaded).toBe(false)
  })

  it('drops a pre-reset pill read once the reset has been followed by a new one', async () => {
    let release!: () => void
    let begin!: () => void
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    const started = new Promise<void>((resolve) => {
      begin = resolve
    })
    server.use(heldSubsystems(gate, begin))
    const stale = useOrgPulseStore.getState().fetchSubsystems()
    // The held handler must have taken the request before the handler set
    // changes underneath it, or the "stale" read is served by the new one.
    await started

    useOrgPulseStore.getState().reset()
    server.use(subsystemsHandler('active'))
    await useOrgPulseStore.getState().fetchSubsystems()

    release()
    await stale

    // The pill reads subsystems alone, so this half is decided by
    // `subsystemRevision` and nothing else. That counter is module state and
    // outlives the store's fields: zeroed on reset, the pre-reset read is
    // handed the very number the post-reset read claims, matches on it, and
    // puts a verdict from before the clear back on the always-mounted pill.
    expect(useOrgPulseStore.getState().subsystems[0]?.phase).toBe('active')
  })

  it('drops a superseded pulse read entirely, blocked tasks and load flags', async () => {
    let release!: () => void
    let begin!: () => void
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    const started = new Promise<void>((resolve) => {
      begin = resolve
    })
    // The task half FAILS, so the stale response carries something visible:
    // an error message the fresh store must not acquire.
    server.use(heldSubsystems(gate, begin), failingTasks())
    const stale = useOrgPulseStore.getState().fetchOrgPulse()
    await started

    useOrgPulseStore.getState().reset()

    release()
    await stale

    // The blocked-task half and the load flags used to apply unconditionally,
    // on the reasoning that only this reader writes them. That is a different
    // claim from "only one of this reader is ever in flight": a superseded
    // response wrote its own outcome over a store that had just been cleared
    // and marked it as already read.
    const state = useOrgPulseStore.getState()
    expect(state.blockedTasksError).toBeNull()
    expect(state.loaded).toBe(false)
    expect(state.loading).toBe(false)
  })
})
