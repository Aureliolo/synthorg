import '@testing-library/jest-dom/vitest'
import { createElement } from 'react'
import type { ComponentProps, ReactNode, Ref } from 'react'
import { afterAll, afterEach, beforeAll, beforeEach, vi } from 'vitest'
import { MotionGlobalConfig } from 'motion/react'
import { setupServer } from 'msw/node'
import { useToastStore } from '@/stores/toast'
import { useThemeStore } from '@/stores/theme'
import { cancelPendingPersist } from '@/stores/notifications'
import { defaultHandlers } from '@/mocks/handlers'

// jsdom's `document.cookie` is backed by `tough-cookie`'s Promise-based
// `CookieJar`. Every get/set -- including read-only reads from our CSRF
// interceptor (`api/client.ts` -> `utils/csrf.ts` -> `document.cookie`) and
// MSW 2.x's own cookie-jar reads (`getAllDocumentCookies` during handler
// setup) -- schedules a `createPromiseCallback` that Vitest's
// `--detect-async-leaks` treats as a leaked Promise. 19 of the 69 pre-shim
// baseline leaks traced back to this path (17 from `getCsrfToken` /
// `getAllDocumentCookies` plus 2 adjacent tough-cookie frames); see
// `docs/design/web-http-adapter.md` for the full investigation.
//
// Replace the jsdom descriptor on `Document.prototype` (not on the
// instance: `getCsrfToken`-style reads resolve through the prototype chain,
// and `__tests__/utils/csrf.test.ts` layers its own `Document.prototype`
// mocks on top -- capturing this shim's descriptor as its "original" and
// restoring it in `afterEach`). Semantics preserved: `document.cookie`
// stringifies the jar as `k=v; k=v`; assignment parses the first `k=v`
// pair, and delete-style writes (`Max-Age=0` or a past `Expires=`) remove
// the entry so `utils/app-version.ts::clearClientVisibleCookies` behaves
// like the real browser. Prod is untouched: this file is only loaded via
// `vitest.config.ts::setupFiles` and never enters the Vite production
// bundle; the `typeof document !== 'undefined'` check is defense-in-depth.
const CSRF_SEED_VALUE = 'test-csrf-token'
const cookieJar: Record<string, string> = Object.create(null) as Record<
  string,
  string
>
if (typeof document !== 'undefined') {
  Object.defineProperty(Document.prototype, 'cookie', {
    configurable: true,
    get: () =>
      Object.entries(cookieJar)
        .map(([k, v]) => `${k}=${v}`)
        .join('; '),
    set: (raw: string) => {
      if (typeof raw !== 'string') return
      const segments = raw.split(';')
      const pair = segments[0] ?? ''
      const eq = pair.indexOf('=')
      if (eq === -1) return
      const name = pair.slice(0, eq).trim()
      const value = pair.slice(eq + 1).trim()
      // Belt-and-suspenders prototype-pollution guard. The jar uses
      // `Object.create(null)` (no prototype chain), so none of these names
      // can actually pollute `Object.prototype` through `cookieJar[name] =
      // value`. We still reject them so the shim never stores a key that a
      // future refactor (spread, `Object.assign`, iteration into a
      // prototype-carrying object) could weaponise. Diverging from RFC 6265
      // is acceptable here because production code never emits these names
      // as cookies.
      if (
        !name ||
        name === '__proto__' ||
        name === 'constructor' ||
        name === 'prototype'
      )
        return
      const isDelete = segments.slice(1).some((segment) => {
        const attr = segment.trim().toLowerCase()
        if (attr === 'max-age=0') return true
        if (!attr.startsWith('expires=')) return false
        const expiresAt = Date.parse(attr.slice('expires='.length))
        return Number.isFinite(expiresAt) && expiresAt <= Date.now()
      })
      if (isDelete) {
        delete cookieJar[name]
        return
      }
      cookieJar[name] = value
    },
  })
}

// Vitest 4's ``BenchmarkUserOptions`` has no ``setupFiles`` field, so
// ``vitest bench`` inherits ``test.setupFiles`` and loads this module
// alongside every ``.bench.ts`` file. MSW's ``setupServer`` patches
// Node's global ``http.request`` interceptor on ``listen()`` and
// throws ``Invariant Violation`` if it is called more than once
// against the same global without an intervening ``close()``; with
// five ``.bench.ts`` files sharing one worker the second listen
// trips that invariant and fails the whole CodSpeed Web job. Bench
// files never hit network anyway -- they exercise pure helpers --
// so we skip MSW wiring entirely in bench mode. ``process.argv``
// reliably contains ``'bench'`` when invoked via ``vitest bench``
// (the shape ``npm --prefix web run bench`` uses); the
// ``test-setup`` cookie / matchMedia / rAF shims still apply so
// benches that touch DOM-adjacent helpers (``csrf.bench.ts``)
// still see a working ``document.cookie``.
const IS_BENCH = process.argv.includes('bench')

// Global MSW server: every default endpoint handler is registered up front
// so tests that do not configure their own overrides get a predictable
// happy-path response for any request. Requests that fall through to a
// path with no handler fail the test loudly (`onUnhandledRequest: 'error'`)
// so new endpoints cannot ship without a matching default handler.
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
  if (!IS_BENCH) {
    server.listen({ onUnhandledRequest: 'error' })
  }
})

afterEach(() => {
  if (!IS_BENCH) {
    server.resetHandlers()
  }
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
  if (!IS_BENCH) {
    server.close()
  }
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
  // Theme store subscribes to a `prefers-reduced-motion` MediaQueryList
  // at factory time; detach the listener here so
  // `--detect-async-leaks` does not count it per-test. Paired with
  // the ``reattach()`` in the ``beforeEach`` above.
  useThemeStore.getState().teardown()
})
