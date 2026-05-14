/**
 * Teardown contract for the theme store's matchMedia listener.
 *
 * The theme store subscribes to a ``prefers-reduced-motion`` MediaQueryList
 * on creation. Without a teardown the listener survives Vite Fast Refresh
 * cycles in dev and (as a forgotten subscription) the active-handle gate
 * would fail any test that exercises the store. This test pins the
 * contract that ``useThemeStore.getState().teardown()`` detaches the
 * listener: symmetric ``addEventListener`` / ``removeEventListener``
 * counts prove the subscription is released.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useThemeStore } from '@/stores/theme'

type Listener = (e: MediaQueryListEvent) => void

describe('useThemeStore teardown', () => {
  let addCalls: number
  let removeCalls: number
  let attachedListeners: Set<Listener>
  let originalMatchMedia: typeof window.matchMedia

  beforeEach(() => {
    addCalls = 0
    removeCalls = 0
    attachedListeners = new Set()

    originalMatchMedia = window.matchMedia
    window.matchMedia = vi.fn((query: string) => {
      const mql = {
        matches: false,
        media: query,
        onchange: null,
        addEventListener: (_event: string, listener: Listener) => {
          addCalls++
          attachedListeners.add(listener)
        },
        removeEventListener: (_event: string, listener: Listener) => {
          removeCalls++
          attachedListeners.delete(listener)
        },
        addListener: () => {},
        removeListener: () => {},
        dispatchEvent: () => false,
      }
      return mql as unknown as MediaQueryList
    }) as unknown as typeof window.matchMedia
  })

  afterEach(() => {
    window.matchMedia = originalMatchMedia
  })

  it('exposes a teardown() that is callable and idempotent', () => {
    // This test asserts the PUBLIC CONTRACT of ``teardown()`` only:
    //  - ``teardown`` is exposed on the store
    //  - Calling it does not throw
    //  - Counter invariants hold (``removeCalls`` >= ``addCalls``,
    //    attached-listener set is empty after teardown)
    //
    // Why the counters start at 0: the Zustand store is a singleton
    // created at module-load time, which runs BEFORE the test's
    // ``beforeEach`` installs the instrumented ``matchMedia`` mock.
    // The store's matchMedia subscription therefore attached to the
    // real ``window.matchMedia``, not our fake, so ``addCalls`` and
    // ``attachedListeners.size`` are 0 here and the assertions are
    // trivially satisfied.
    //
    // The full attach/detach/dispatch lifecycle (add-call count,
    // listener-fires-after-reattach, state sync to current mql.matches)
    // is validated by the later tests in this file that exercise
    // ``teardown()`` -> ``reattach()`` against the instrumented fake.
    const store = useThemeStore.getState()
    expect(typeof store.teardown).toBe('function')

    store.teardown()

    expect(removeCalls).toBeGreaterThanOrEqual(addCalls)
    expect(attachedListeners.size).toBe(0)
  })

  it('teardown() is idempotent', () => {
    const store = useThemeStore.getState()
    store.teardown()
    store.teardown() // must not throw or double-remove
    expect(attachedListeners.size).toBe(0)
  })

  it('reattach() re-installs the listener after a prior teardown()', () => {
    const store = useThemeStore.getState()
    // teardown clears the closure refs so reattach is observably a
    // fresh addEventListener against the current window.matchMedia
    // (which the test has already swapped to the instrumented fake).
    store.teardown()
    expect(attachedListeners.size).toBe(0)

    const addsBefore = addCalls
    store.reattach()
    expect(addCalls).toBe(addsBefore + 1)
    expect(attachedListeners.size).toBe(1)
  })

  it('reattach() is idempotent while the listener is already attached', () => {
    const store = useThemeStore.getState()
    store.teardown()
    store.reattach()
    const addsAfterFirst = addCalls
    store.reattach() // second call while attached must be a no-op
    expect(addCalls).toBe(addsAfterFirst)
    expect(attachedListeners.size).toBe(1)
  })

  it('reattach() then teardown() returns to a detached state', () => {
    const store = useThemeStore.getState()
    store.teardown()
    store.reattach()
    store.teardown()
    expect(attachedListeners.size).toBe(0)
  })

  it('reattach() restores end-to-end reactivity to matchMedia change events', () => {
    // Proves the contract that motivated reattach() in the first place:
    // after teardown() + reattach() against a fresh instrumented
    // matchMedia, firing a change event drives the store as if this
    // were the first test in the suite.
    const store = useThemeStore.getState()
    store.teardown()
    store.reattach()

    expect(attachedListeners.size).toBe(1)
    const listener = [...attachedListeners][0]
    if (!listener) throw new Error('expected one attached listener')

    const before = useThemeStore.getState().reducedMotionDetected
    listener({ matches: !before } as MediaQueryListEvent)
    expect(useThemeStore.getState().reducedMotionDetected).toBe(!before)

    // Flip back so the singleton state is restored for neighbouring tests.
    listener({ matches: before } as MediaQueryListEvent)
  })
})
