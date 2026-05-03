import '@testing-library/jest-dom/vitest'
import { createElement } from 'react'
import type { ComponentProps, ReactNode, Ref } from 'react'
import { afterAll, afterEach, beforeAll, beforeEach, vi } from 'vitest'
import { MotionGlobalConfig } from 'motion/react'
import { setupServer } from 'msw/node'
import { cancelPendingPersist } from '@/stores/notifications'
import { cancelSetupWizardPersist } from '@/stores/setup-wizard/teardown'
import { useThemeStore } from '@/stores/theme'
import { useToastStore } from '@/stores/toast'
// NOTE: meetings, approvals, scaling stores are intentionally NOT
// imported (statically OR dynamically) from this global setup file
// because doing so transitively loads `@/api/client` and runs its
// import-time side effects -- the module-level `axios.create(...)`
// call and the `apiClient.interceptors.request.use(...)` registration
// that captures a live binding to `@/utils/csrf`. Once those side
// effects have fired, per-test `vi.mock('@/utils/csrf', ...)` and
// `vi.mock('axios', ...)` setups are too late to retroactively
// replace what the interceptor closure already resolved, so any
// test that mocks one of those modules sees real-module behaviour
// instead of its mock.
//
// The fix is structural, not "skip the cleanup": when a domain
// store eventually schedules a real timer / listener, expose its
// teardown through a *side-effect-free* entrypoint -- e.g. a
// dedicated ``@/stores/<name>/teardown`` module that re-exports
// only a plain ``teardown(): void`` callable and does NOT
// transitively load `@/api/client`. This file can then import that
// thin shim from the global ``afterEach`` without poisoning
// per-test mocks. The contract from web/CLAUDE.md ("any new store
// that schedules timers ... must expose an equivalent cleanup hook
// and register it in the global afterEach") is preserved; the
// constraint is only on HOW the hook is reached.
import { defaultHandlers } from '@/mocks/handlers'
import { cookieJar, installCookieShim } from '@/cookie-shim'
import { installStorageShim } from '@/storage-shim'
import { cancelOrgChartPrefsPersist } from '@/stores/org-chart-prefs-teardown'

// jsdom's `document.cookie` is backed by `tough-cookie`'s Promise-based
// `CookieJar`, which schedules a `createPromiseCallback` for every
// get/set and inflates `--detect-async-leaks`. The shim shared with
// `bench-setup.ts` lives in `@/cookie-shim` and replaces the
// `Document.prototype` descriptor with a synchronous in-memory jar.
// Behaviour is documented in that module; the jar is exported so the
// per-test teardown below can reset cookie state without re-touching
// the DOM (avoiding jsdom's tough-cookie cost).
const CSRF_SEED_VALUE = 'test-csrf-token'
installCookieShim()

// jsdom's `window.localStorage` / `sessionStorage` schedule a
// `setTimeout(0)` per write to dispatch a `storage` event. The
// dashboard touches localStorage from two paths: Zustand `persist`
// middleware (setup-wizard, org-chart-prefs) and direct
// `localStorage.setItem` calls (theme, notifications), so any test
// that mutates one of those stores contributed Timeout leaks. The
// shim in `@/storage-shim` patches `Storage.prototype` so writes go
// through a Map-backed in-memory store; no app or test code
// subscribes to the `storage` event, so the dispatch is dead weight
// in the test runner.
installStorageShim()

// Global MSW server: every default endpoint handler is registered up front
// so tests that do not configure their own overrides get a predictable
// happy-path response for any request. Requests that fall through to a
// path with no handler fail the test loudly (`onUnhandledRequest: 'error'`)
// so new endpoints cannot ship without a matching default handler.
//
// This file is loaded ONLY by the ``unit`` project in
// ``vitest.config.ts``; the ``bench`` project loads
// ``./src/bench-setup.ts`` (no MSW, no React, no Motion). Splitting
// the two projects is the architectural fix for CodSpeed Web --
// previously ``test.setupFiles`` was shared with bench mode, and
// MSW's ``setupServer().listen()`` tripped its global-interceptor
// invariant on the second ``.bench.ts`` file.
export const server = setupServer(...defaultHandlers)

beforeAll(() => {
  // The axios client attaches X-CSRF-Token on mutating requests by reading
  // the `csrf_token` cookie. Seed it here so every POST/PUT/PATCH/DELETE
  // test sends the header without having to log in first. Seeding in
  // `beforeAll` (not `beforeEach`) is deliberate: every `document.cookie`
  // assignment in jsdom flows through tough-cookie's Promise-based API,
  // and doing it 2574 times inflates `--detect-async-leaks` counts (this
  // was the cause of the round-2 14-leak regression). The test dispatcher
  // does not validate the value.
  document.cookie = `csrf_token=${CSRF_SEED_VALUE}; path=/`
  server.listen({ onUnhandledRequest: 'error' })
})

afterEach(() => {
  server.resetHandlers()
  // Clear any cookies a test wrote to the jar so state cannot leak across
  // tests in the same Vitest worker, then restore the global CSRF seed so
  // mutating-request tests still send `X-CSRF-Token` without re-seeding
  // through `document.cookie` (which would re-introduce the leak that the
  // shim was introduced to fix).
  for (const name of Object.keys(cookieJar)) {
    delete cookieJar[name]
  }
  cookieJar.csrf_token = CSRF_SEED_VALUE
})

afterAll(() => {
  server.close()
})

// Short-circuit every Motion animation so framer-motion does not leave
// `AnimationComplete` promise chains pending past test teardown. This is
// the canonical test hook documented at https://motion.dev/docs/testing
// and resolves animation promises instantly instead of via rAF.
MotionGlobalConfig.skipAnimations = true

// Even with `skipAnimations`, framer-motion still creates a Promise in
// `MotionValue.start` and schedules its resolution through the next frame
// (rAF, polyfilled by jsdom as setInterval). Under vitest with
// `--detect-async-leaks` those promises are flagged. Replacing `motion.*`
// with plain host elements and `AnimatePresence` with a passthrough removes
// the animation code path entirely. Tests that assert on motion-specific
// behavior can still opt out via their own `vi.mock('motion/react', ...)`.
vi.mock('motion/react', async () => {
  const actual = await vi.importActual<typeof import('motion/react')>('motion/react')

  type MotionStubProps = ComponentProps<'div'> & {
    ref?: Ref<HTMLElement>
    children?: ReactNode
  } & Record<string, unknown>

  const MOTION_ONLY_PROPS = new Set([
    'animate', 'initial', 'exit', 'transition', 'variants', 'whileHover',
    'whileTap', 'whileFocus', 'whileDrag', 'whileInView', 'layout',
    'layoutId', 'layoutDependency', 'layoutScroll', 'drag', 'dragConstraints',
    'dragElastic', 'dragMomentum', 'dragTransition', 'dragSnapToOrigin',
    'dragControls', 'dragListener', 'onAnimationStart', 'onAnimationComplete',
    'onUpdate', 'onDragStart', 'onDrag', 'onDragEnd', 'onDirectionLock',
    'onHoverStart', 'onHoverEnd', 'onTapStart', 'onTap', 'onTapCancel',
    'onViewportEnter', 'onViewportLeave', 'viewport', 'custom', 'inherit',
  ])

  const makeMotionComponent = (tag: string) => {
    return function MotionStub({ children, ref, style, ...rest }: MotionStubProps) {
      const domProps: Record<string, unknown> = {}
      for (const [key, value] of Object.entries(rest)) {
        if (!MOTION_ONLY_PROPS.has(key)) domProps[key] = value
      }
      // Preserve plain-object style values; drop motion-value-backed entries.
      const plainStyle =
        style && typeof style === 'object'
          ? Object.fromEntries(
              Object.entries(style).filter(
                ([, v]) =>
                  v === null
                  || ['string', 'number', 'boolean'].includes(typeof v),
              ),
            )
          : undefined
      return createElement(
        tag,
        { ref, style: plainStyle, ...domProps },
        children,
      )
    }
  }

  const motionProxy = new Proxy({} as typeof actual.motion, {
    get(_target, prop) {
      if (typeof prop !== 'string') return undefined
      return makeMotionComponent(prop)
    },
  })

  return {
    ...actual,
    motion: motionProxy,
    AnimatePresence: ({ children }: { children?: ReactNode }) => <>{children}</>,
  }
})

// jsdom polyfills `requestAnimationFrame` with a shared `setInterval` that
// only clears when every registered callback has fired. Recharts's
// `ZIndexPortal` registers rAF callbacks via @reduxjs/toolkit that keep
// getting re-scheduled, so the interval outlives the test and
// --detect-async-leaks flags it as a Timeout leak. Replace rAF with
// `setTimeout(cb, 0)` so each frame is a discrete macrotask that drains
// cleanly between tests.
//
// We intentionally do NOT drain pending rAF callbacks in the global
// afterEach: d3-timer (used by d3-force in `pages/org/force-layout.ts`)
// binds `setFrame` to our shim at module load time and relies on its
// wake() callback firing to clear its internal `setInterval(poke)` after
// `simulation.stop()`. Clearing the shim's setTimeout handles before
// wake() can run strands that interval and reintroduces a leak.
if (typeof window !== 'undefined') {
  const timers = new Set<ReturnType<typeof setTimeout>>()
  window.requestAnimationFrame = (callback: FrameRequestCallback): number => {
    const handle = setTimeout(() => {
      timers.delete(handle)
      callback(performance.now())
    }, 0)
    timers.add(handle)
    // type-cast: cross-env timer ID -- @types/node says setTimeout returns
    // Timeout, the DOM rAF contract says it returns number. There is no
    // shared base type, and jsdom's setTimeout produces a number at
    // runtime, so the cast is the bridge.
    return handle as unknown as number
  }
  window.cancelAnimationFrame = (handle: number) => {
    // type-cast: cross-env timer ID (see requestAnimationFrame above).
    clearTimeout(handle as unknown as ReturnType<typeof setTimeout>)
    timers.delete(handle as unknown as ReturnType<typeof setTimeout>)
  }
}

// jsdom does not implement matchMedia; several components (the breakpoint
// hook, the theme store, a few prefers-* consumers) call it during render.
// Provide a no-op shim that reports `matches: false` for every query so the
// default render path is used. Motion's animation short-circuit is handled
// by the mock above; we explicitly do NOT force reduced-motion here because
// hook tests (useFlash, useCountAnimation) pin their behavior to the
// non-reduced branch.
if (typeof window !== 'undefined' && typeof window.matchMedia !== 'function') {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    configurable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  })
}

// Toast store schedules a `setTimeout` per auto-dismiss (success / info toasts
// with a real timer). Without a global teardown hook these timers survive the
// test boundary and vitest flags them as leaked. `dismissAll()` clears both
// the pending handles and the toasts array in one idiomatic call; tests that
// need to inspect the toasts list after pending timers drain can instead call
// `cancelAllPending()` directly in their own teardown.
//
// We run this in `afterEach` (not `beforeEach`) deliberately: the test body's
// assertions on toast state complete *before* the afterEach fires, so
// resetting here does not mask in-test assertions. A test that needs toast
// state to persist across a teardown boundary (e.g. asserting a toast is
// still visible after a dialog closes) should inline its own assertion
// within the test body, never rely on post-teardown state.
beforeEach(() => {
  // Reattach the theme store's ``prefers-reduced-motion`` listener
  // for every test, paired with the ``teardown()`` in ``afterEach``
  // below. Without this, the singleton store would stop reacting to
  // OS preference changes after the first test's afterEach ran, and
  // any subsequent test that exercises reduced-motion reactivity
  // would false-pass silently.
  //
  // ``beforeEach`` runs BEFORE the test body, so it cannot pick up a
  // ``window.matchMedia`` mock that the test installs later in the
  // body. Tests that need runtime reactivity against a mocked
  // ``matchMedia`` must call ``useThemeStore.getState().reattach()``
  // themselves after installing their mock -- this beforeEach only
  // restores the listener against whatever ``matchMedia`` is present
  // at the moment it runs (typically the default ``test-setup`` mock).
  useThemeStore.getState().reattach()
})

afterEach(() => {
  useToastStore.getState().dismissAll()
  // Notifications store debounces localStorage persistence with a 300ms
  // setTimeout; drop any pending handle so it does not outlive the test.
  cancelPendingPersist()
  // Setup-wizard store wraps itself in Zustand ``persist``; clear the
  // localStorage key directly via the side-effect-free teardown shim
  // so we do not transitively load ``@/api/client`` (see top-of-file
  // comment).  The shim is a no-op when localStorage is unavailable.
  cancelSetupWizardPersist()
  // Org-chart-prefs store also uses Zustand ``persist``; same
  // side-effect-free teardown pattern -- drops the persisted key so
  // toolbar toggles a test sets do not bleed into the next test in
  // the same Vitest worker.
  cancelOrgChartPrefsPersist()
  // Theme store subscribes to a `prefers-reduced-motion` MediaQueryList
  // at factory time; detach the listener here so
  // `--detect-async-leaks` does not count it per-test. Paired with
  // the ``reattach()`` in the ``beforeEach`` above.
  useThemeStore.getState().teardown()
})
