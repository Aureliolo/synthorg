import { http, HttpResponse } from 'msw'
import { renderedSnapshot, resetHealthStore, useHealthStore } from '@/stores/health'
import { successFor } from '@/mocks/handlers'
import { server } from '@/test-setup'
import type { getHealthDetail } from '@/api/endpoints/health'
import type { HealthStatus } from '@/api/types/system'

/**
 * The store is shared by the status pill and the health dialog, so an
 * out-of-order response now produces visibly inconsistent UI across both rather
 * than being contained to one component's dead state. These tests drive real
 * overlapping requests against MSW instead of mutating the store directly.
 */

function body(overrides: Partial<HealthStatus> = {}) {
  return successFor<typeof getHealthDetail>({
    status: 'ok',
    persistence: true,
    message_bus: true,
    providers: true,
    telemetry: 'disabled',
    memory: { state: 'durable', backend: 'sqlvector', detail: null },
    backup: { state: 'wired', detail: null },
    version: '0.0.0-test',
    uptime_seconds: 1,
    ...overrides,
  })
}

interface ResponseGate {
  /** Awaited by a handler to hold its response back, or by a test to observe it. */
  readonly opened: Promise<void>
  /** Lets the held response go, or signals that the handler has been reached. */
  readonly open: () => void
}

/**
 * A handler-side hold on a response, released only when the test says so.
 *
 * Slowness is expressed as a gate rather than a timer because the store now
 * aborts a superseded probe, which abandons the handler mid-await: a timer left
 * running past the test end is a leaked handle the active-handle gate fails on.
 * It also makes "the response had not been sent yet" an exact claim rather than
 * a race against a delay.
 *
 * Each test below uses a second gate purely as an arrival signal. Branching a
 * handler on a call counter would otherwise be timing-dependent now that the
 * store aborts: the second `fetchHealth()` supersedes the first synchronously,
 * so if the abort beats MSW's dispatch of the first request the counter never
 * reaches 1 and the *second* probe takes the hold-open branch, hanging on a gate
 * the test only opens afterwards.
 */
function responseGate(): ResponseGate {
  let open!: () => void
  const opened = new Promise<void>((resolve) => {
    open = resolve
  })
  return { opened, open }
}

describe('useHealthStore probe ordering', () => {
  it('discards a slow probe that a newer one has already superseded', async () => {
    // The first request answers `unavailable` once released; the second answers
    // `ok` immediately. The stale outage verdict must never reach the pill,
    // which has already been told the system is serving.
    const arrived = responseGate()
    const gate = responseGate()
    let call = 0
    server.use(
      http.get('/api/v1/health', async () => {
        call += 1
        if (call === 1) {
          arrived.open()
          await gate.opened
          return HttpResponse.json(body({ status: 'unavailable', persistence: false }))
        }
        return HttpResponse.json(body())
      }),
    )

    const slow = useHealthStore.getState().fetchHealth()
    await arrived.opened
    const fast = useHealthStore.getState().fetchHealth()
    await fast
    await slow

    const snapshot = renderedSnapshot(useHealthStore.getState().loadState)
    expect(snapshot?.data.status).toBe('ok')
    expect(snapshot?.data.persistence).toBe(true)
    gate.open()
  })

  it('discards a superseded probe that fails, not only one that succeeds', async () => {
    // The failure path needs the same treatment: a slow transport failure
    // landing after a fresh success would report the backend down while it is
    // serving.
    const arrived = responseGate()
    const gate = responseGate()
    let call = 0
    server.use(
      http.get('/api/v1/health', async () => {
        call += 1
        if (call === 1) {
          arrived.open()
          await gate.opened
          return HttpResponse.error()
        }
        return HttpResponse.json(body())
      }),
    )

    const slow = useHealthStore.getState().fetchHealth()
    await arrived.opened
    const fast = useHealthStore.getState().fetchHealth()
    await fast
    await slow

    expect(useHealthStore.getState().loadState.state).toBe('ok')
    gate.open()
  })

  it('applies the newest outcome even when it is the failure', async () => {
    // Success is not privileged: if the latest probe genuinely failed, that is
    // the current truth.
    const arrived = responseGate()
    const gate = responseGate()
    let call = 0
    server.use(
      http.get('/api/v1/health', async () => {
        call += 1
        if (call === 1) {
          arrived.open()
          await gate.opened
          return HttpResponse.json(body())
        }
        return HttpResponse.error()
      }),
    )

    const slow = useHealthStore.getState().fetchHealth()
    await arrived.opened
    const fast = useHealthStore.getState().fetchHealth()
    await fast
    await slow

    expect(useHealthStore.getState().loadState.state).toBe('error')
    gate.open()
  })

  it('releases the superseded probe instead of waiting out its response', async () => {
    // The gate is deliberately still shut when the superseded probe is awaited:
    // its response has not been sent, so it can only settle by having been
    // released. Ignoring the response instead would hold the request open for
    // the client's whole timeout, and this await would never return.
    const arrived = responseGate()
    const gate = responseGate()
    let call = 0
    server.use(
      http.get('/api/v1/health', async () => {
        call += 1
        if (call === 1) {
          arrived.open()
          await gate.opened
          return HttpResponse.json(body())
        }
        return HttpResponse.json(body())
      }),
    )

    const superseded = useHealthStore.getState().fetchHealth()
    // Its request is provably open and parked on the shut gate before the
    // supersede, so the await below can only return by release.
    await arrived.opened
    await useHealthStore.getState().fetchHealth()
    await superseded

    expect(useHealthStore.getState().loadState.state).toBe('ok')
    gate.open()
  })

  it('drops an in-flight probe invalidated by a reset', async () => {
    // The counter is bumped rather than zeroed precisely so a request already
    // in flight from a torn-down test cannot land on the next test's state.
    const gate = responseGate()
    server.use(
      http.get('/api/v1/health', async () => {
        await gate.opened
        return HttpResponse.json(body())
      }),
    )

    const inFlight = useHealthStore.getState().fetchHealth()
    resetHealthStore()
    await inFlight

    expect(useHealthStore.getState().loadState.state).toBe('idle')
    gate.open()
  })
})

describe('useHealthStore cancellation', () => {
  it('falls back to the snapshot the cancelled probe was refreshing', async () => {
    // A surface going away must not leave the store stuck on "checking...", and
    // the snapshot it falls back to keeps its own timestamp: it is a moment old,
    // and saying otherwise would misreport how fresh the data is.
    server.use(http.get('/api/v1/health', () => HttpResponse.json(body())))
    await useHealthStore.getState().fetchHealth()
    const settled = renderedSnapshot(useHealthStore.getState().loadState)

    const gate = responseGate()
    server.use(
      http.get('/api/v1/health', async () => {
        await gate.opened
        return HttpResponse.json(body())
      }),
    )
    const cancelled = useHealthStore.getState().fetchHealth()
    useHealthStore.getState().cancelProbe()
    await cancelled

    const loadState = useHealthStore.getState().loadState
    expect(loadState.state).toBe('ok')
    expect(renderedSnapshot(loadState)?.fetchedAt).toEqual(settled?.fetchedAt)
    gate.open()
  })

  it('goes idle when the cancelled probe had nothing settled behind it', async () => {
    const gate = responseGate()
    server.use(
      http.get('/api/v1/health', async () => {
        await gate.opened
        return HttpResponse.json(body())
      }),
    )

    const cancelled = useHealthStore.getState().fetchHealth()
    useHealthStore.getState().cancelProbe()
    await cancelled

    expect(useHealthStore.getState().loadState.state).toBe('idle')
    gate.open()
  })
})

describe('useHealthStore refresh semantics', () => {
  it('keeps showing the settled snapshot while refreshing over it', async () => {
    // Every poll tick and every dialog open calls fetch. Wiping the snapshot to
    // `loading` first made the pill blink through "checking..." on each one.
    server.use(http.get('/api/v1/health', () => HttpResponse.json(body())))
    await useHealthStore.getState().fetchHealth()

    const gate = responseGate()
    server.use(
      http.get('/api/v1/health', async () => {
        await gate.opened
        return HttpResponse.json(body())
      }),
    )
    const refreshing = useHealthStore.getState().fetchHealth()

    const during = useHealthStore.getState().loadState
    expect(during.state).toBe('loading')
    expect(renderedSnapshot(during)?.data.status).toBe('ok')

    gate.open()
    await refreshing
  })

  it('carries no snapshot on the very first probe', async () => {
    const gate = responseGate()
    server.use(
      http.get('/api/v1/health', async () => {
        await gate.opened
        return HttpResponse.json(body())
      }),
    )
    const first = useHealthStore.getState().fetchHealth()

    expect(renderedSnapshot(useHealthStore.getState().loadState)).toBeNull()

    gate.open()
    await first
  })

  it('drops the snapshot once a probe genuinely fails', async () => {
    // An error is not stale-but-good data: the surfaces must stop presenting a
    // snapshot as current once the backend has stopped answering.
    server.use(http.get('/api/v1/health', () => HttpResponse.json(body())))
    await useHealthStore.getState().fetchHealth()

    server.use(http.get('/api/v1/health', () => HttpResponse.error()))
    await useHealthStore.getState().fetchHealth()

    expect(renderedSnapshot(useHealthStore.getState().loadState)).toBeNull()
  })
})
