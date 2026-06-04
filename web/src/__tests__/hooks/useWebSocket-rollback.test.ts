/**
 * Rollback contract for the useWebSocket handler-wiring loop.
 *
 * If any ``onChannelEvent`` call throws partway through the binding
 * loop, the cleanup function must only deregister the handlers that
 * were actually registered -- not blindly iterate every binding and
 * call ``offChannelEvent`` for handlers that were never wired. Without
 * the rollback, the store's internal handler set keeps stale
 * references across reconnects and the cleanup iterates phantom
 * entries.
 */

import { cleanup, renderHook } from '@testing-library/react'
import * as fc from 'fast-check'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useWebSocketStore } from '@/stores/websocket'
import { useAuthStore } from '@/stores/auth'

function resetStores() {
  sessionStorage.clear()
  localStorage.clear()
  useAuthStore.setState({
    authStatus: 'authenticated',
    user: null,
    loading: false,
  })
  useWebSocketStore.getState().disconnect()
  useWebSocketStore.setState({
    connected: true,
    reconnectExhausted: false,
    subscribedChannels: [],
  })
}

describe('useWebSocket registration rollback', () => {
  beforeEach(() => {
    // ``restoreAllMocks`` (not ``clearAllMocks``) so
    // ``mockImplementationOnce`` queues from the previous test are
    // fully torn down. ``clearAllMocks`` only wipes call history;
    // any leftover implementation queue from a prior test would leak
    // into the next, bypassing the real store path.
    vi.restoreAllMocks()
    resetStores()
  })

  it('only deregisters handlers it successfully registered', async () => {
    const store = useWebSocketStore.getState()

    const handlerA = vi.fn()
    const handlerB = vi.fn()
    const handlerC = vi.fn()
    const handlerD = vi.fn()

    // Fail the third onChannelEvent call so the setup loop aborts
    // partway through -- handlers A and B registered, C threw,
    // D never attempted.
    const onChannelSpy = vi
      .spyOn(store, 'onChannelEvent')
      .mockImplementationOnce(() => {
        // A registers
      })
      .mockImplementationOnce(() => {
        // B registers
      })
      .mockImplementationOnce(() => {
        throw new Error('wiring failure')
      })
      .mockImplementationOnce(() => {
        // D should not be attempted after C throws
      })

    const offChannelSpy = vi.spyOn(store, 'offChannelEvent')
    const unsubscribeSpy = vi.spyOn(store, 'unsubscribe')

    const { unmount } = renderHook(() =>
      useWebSocket({
        bindings: [
          { channel: 'tasks', handler: handlerA },
          { channel: 'agents', handler: handlerB },
          { channel: 'approvals', handler: handlerC },
          { channel: 'meetings', handler: handlerD },
        ],
      }),
    )

    // Wait for the effect's setup() loop to reach the registration
    // phase. The store is pre-set to ``connected: true`` so the
    // connect() step is a no-op, but subscribe() still schedules a
    // microtask pair before the handler loop starts. ``vi.waitFor``
    // polls the instrumented spy so we don't depend on a fixed
    // number of ``Promise.resolve()`` flushes -- if the hook grows
    // another await step later the test still settles correctly.
    await vi.waitFor(() => {
      // Three registrations: A, B, then C throws. D never attempts.
      expect(onChannelSpy).toHaveBeenCalledTimes(3)
    })

    unmount()

    // Exactly the handlers we registered should be deregistered;
    // no stale phantom entries for C (which threw) or D (which was
    // never attempted).
    const registeredHandlers = onChannelSpy.mock.calls
      .filter((_, idx) => idx < 2)
      .map(([, handler]) => handler)
    const deregisteredHandlers = offChannelSpy.mock.calls.map(
      ([, handler]) => handler,
    )

    expect(deregisteredHandlers).toEqual(registeredHandlers)
    expect(deregisteredHandlers).not.toContain(handlerC)
    expect(deregisteredHandlers).not.toContain(handlerD)

    // Verify the registration loop aborted after C threw: exactly
    // three onChannelEvent invocations (A, B, C) and D never
    // attempted. Without this assertion a regression that kept
    // wiring after a throw could silently register D in the store
    // even though the hook's ledger didn't record it.
    expect(onChannelSpy).toHaveBeenCalledTimes(3)
    expect(onChannelSpy).not.toHaveBeenCalledWith('meetings', handlerD)

    // Channel-level unsubscribe must cover every channel the hook
    // subscribed to (``uniqueChannels`` from the bindings), even the
    // ones where handler wiring aborted mid-loop. Without this the
    // store would keep routing broadcast traffic to the unmounted
    // view through channels we handed it at subscribe time. With the
    // shared-channel guard in ``rollbackSubscriptions``, every channel
    // in this test lands in the unsubscribe arg because no sibling
    // hook is keeping a handler alive -- the next test covers the
    // sibling case.
    expect(unsubscribeSpy).toHaveBeenCalledWith([
      'tasks',
      'agents',
      'approvals',
      'meetings',
    ])
  })

  it('does not unsubscribe a channel that another hook still holds', async () => {
    // Two hook mounts share the ``tasks`` channel. Unmounting the
    // first must leave ``tasks`` subscribed on the store because the
    // second hook's handler is still registered; otherwise the second
    // hook stops receiving broadcast traffic mid-session.
    const store = useWebSocketStore.getState()
    const handlerA = vi.fn()
    const handlerB = vi.fn()

    const unsubscribeSpy = vi.spyOn(store, 'unsubscribe')
    const onChannelSpy = vi.spyOn(store, 'onChannelEvent')

    const first = renderHook(() =>
      useWebSocket({ bindings: [{ channel: 'tasks', handler: handlerA }] }),
    )
    const second = renderHook(() =>
      useWebSocket({ bindings: [{ channel: 'tasks', handler: handlerB }] }),
    )

    // Wait for both effect setups to reach the registration phase.
    // One binding per hook => 2 successful ``onChannelEvent`` calls
    // when both setups have settled; without this concrete assertion
    // the waitFor predicate would be a trivial truthy check and
    // ``first.unmount()`` could race the second hook's ``setup()``.
    await vi.waitFor(() => {
      expect(onChannelSpy).toHaveBeenCalledTimes(2)
    })

    first.unmount()

    // The first unmount must NOT unsubscribe ``tasks`` because the
    // second hook still has its handler registered. Assert
    // ``unsubscribe`` either was not called or was called only with
    // an empty-after-filtering set -- the production shape is "not
    // called at all" after filtering because the only channel in the
    // first hook's binding set is shared with the second hook.
    expect(unsubscribeSpy).not.toHaveBeenCalled()

    // When the second hook unmounts, ``tasks`` should finally be
    // unsubscribed -- no remaining handlers.
    second.unmount()
    expect(unsubscribeSpy).toHaveBeenCalledWith(['tasks'])
  })

  it('property: rollback deregisters exactly the handlers it registered', async () => {
    // Property-based check of the rollback contract: for any unique
    // binding sequence and any throw index, the cleanup deregisters
    // exactly the handlers that were successfully registered (no
    // phantom off-calls for bindings that threw or were never
    // attempted) and unsubscribes every unique channel the hook
    // handed the store at subscribe time.
    const CHANNELS = ['tasks', 'agents', 'approvals', 'meetings'] as const

    await fc.assert(
      fc.asyncProperty(
        // A non-empty subset of CHANNELS (unique, preserves order).
        fc.subarray([...CHANNELS], { minLength: 1 }),
        // The index into that subset at which onChannelEvent throws.
        fc.nat(),
        async (channels, throwIdxRaw) => {
          // Each property iteration must be fully isolated: tear down
          // any previously mounted React trees (their useEffect
          // cleanups would otherwise fire additional
          // ``offChannelEvent`` / ``unsubscribe`` calls during the
          // next iteration's setup) and reset store + spy state.
          cleanup()
          resetStores()
          vi.restoreAllMocks()

          const throwIdx = throwIdxRaw % channels.length
          const bindings = channels.map((channel) => ({
            channel,
            handler: vi.fn(),
          }))

          const store = useWebSocketStore.getState()
          const onChannelSpy = vi.spyOn(store, 'onChannelEvent')
          const offChannelSpy = vi.spyOn(store, 'offChannelEvent')
          const unsubscribeSpy = vi.spyOn(store, 'unsubscribe')

          // ``vi.spyOn`` returns the existing spy if one already
          // exists for that method; across property iterations the
          // same spy instance is reused. ``mockReset`` clears the
          // ``mockImplementationOnce`` queue and call history so
          // leftover entries from an earlier iteration cannot fire
          // against this iteration's spy.
          onChannelSpy.mockReset()
          offChannelSpy.mockReset()
          unsubscribeSpy.mockReset()

          // Fail the ``throwIdx``-th onChannelEvent call. Earlier
          // calls succeed via the real Map/Set implementation; the
          // throw aborts the registration loop without mutating the
          // ledger for bindings beyond it.
          for (let i = 0; i < throwIdx; i++) {
            onChannelSpy.mockImplementationOnce(() => {})
          }
          onChannelSpy.mockImplementationOnce(() => {
            throw new Error(`wiring failure at ${throwIdx}`)
          })

          const { unmount } = renderHook(() => useWebSocket({ bindings }))

          // Wait for the setup loop to reach the failing index.
          await vi.waitFor(() => {
            expect(onChannelSpy).toHaveBeenCalledTimes(throwIdx + 1)
          })

          unmount()

          // Invariant 1: ``offChannelEvent`` fires exactly for the
          // handlers before the failing index (the ledger ``push``
          // runs AFTER the successful call, so the throwing one is
          // never ledgered).
          const registeredHandlers = bindings
            .slice(0, throwIdx)
            .map((b) => b.handler)
          const deregisteredHandlers = offChannelSpy.mock.calls.map(
            ([, handler]) => handler,
          )
          expect(deregisteredHandlers).toEqual(registeredHandlers)

          // Invariant 2: onChannelEvent was called exactly
          // ``throwIdx + 1`` times (through the throw, not beyond).
          expect(onChannelSpy).toHaveBeenCalledTimes(throwIdx + 1)
          for (let i = throwIdx + 1; i < bindings.length; i++) {
            expect(onChannelSpy).not.toHaveBeenCalledWith(
              bindings[i]!.channel,
              bindings[i]!.handler,
            )
          }

          // Invariant 3: channel-level unsubscribe covers every
          // unique channel in the binding set since no sibling hook
          // is keeping any handler alive in this isolated property
          // run. The order reflects the iteration order of the
          // ``new Set(channels)`` used inside ``rollbackSubscriptions``.
          const uniqueChannels = [...new Set(channels)]
          expect(unsubscribeSpy).toHaveBeenCalledWith(uniqueChannels)
        },
      ),
      { numRuns: 40 },
    )
  })
})
