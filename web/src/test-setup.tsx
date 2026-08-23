import '@testing-library/jest-dom/vitest'
import { createElement } from 'react'
import type { ComponentProps, ReactNode, Ref } from 'react'
import { afterAll, afterEach, beforeAll, beforeEach, vi } from 'vitest'
import { MotionGlobalConfig } from 'motion/react'
import { setupServer } from 'msw/node'
import { cancelPendingMcpCatalogSearch } from '@/stores/mcp-catalog/_state'
import { useOrgConversationStore } from '@/stores/org-conversation'
import { useOrgQuestionsStore } from '@/stores/org-questions'
import { resetMessageIds } from '@/pages/chat/message-id'
import { useSetupWizardStore } from '@/stores/setup-wizard'
import { usePlanCommentsStore } from '@/stores/planComments'
import { usePlanEvaluationStore } from '@/stores/planEvaluation'
import { usePlanForecastStore } from '@/stores/planForecast'
import { usePlansStore } from '@/stores/plans'
import { useThemeStore } from '@/stores/theme'
import { useToastStore } from '@/stores/toast'
// NOTE: meetings and approvals stores are intentionally NOT
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
import { resetOrgChartPrefs } from '@/stores/org-chart-prefs'
import { resetDashboardPrefs } from '@/stores/dashboard-prefs'
import { resetHealthStore } from '@/stores/health'
import { resetOrgPulseStore } from '@/stores/org-pulse'
// Pure module-scope counter (imports nothing), so it is safe here.
import { resetHealthRevision } from '@/stores/providers/health-revision'
import { resetProvidersStore } from '@/stores/providers'
// Pure helper: clears the per-endpoint 429 breaker so a tripped breaker in
// one test cannot leak into the next. The module imports only the logger
// (no `@/api/client` side effects), so it is safe in this global setup.
import { _settleSessionProbeForTests } from '@/api/client'
import { resetCircuitBreaker } from '@/utils/circuit-breaker'
import { resetCapabilitiesCache } from '@/hooks/useCapabilities'

// jsdom's `document.cookie` is backed by `tough-cookie`'s Promise-based
// `CookieJar`, which schedules a `createPromiseCallback` for every
// get/set. The shim shared with `bench-setup.ts` lives in
// `@/cookie-shim` and replaces the `Document.prototype` descriptor
// with a synchronous in-memory jar. Test-speed optimisation;
// prototype-pollution defence is the primary security purpose (see
// the cookie-shim module header). The jar is exported so the
// per-test teardown below can reset cookie state without re-touching
// the DOM.
const CSRF_SEED_VALUE = 'test-csrf-token'
installCookieShim()

// jsdom's `window.localStorage` / `sessionStorage` schedule a
// `setTimeout(0)` per write to dispatch a `storage` event. The dashboard is a
// pure API consumer and persists no app state client-side, but the few
// allowlisted client-storage paths (the auth/CSRF cookie shim, the
// build-version check, per-device canvas/draft state) still touch
// `localStorage`, so a test exercising one adds dispatch overhead per write.
// The shim in `@/storage-shim` patches `Storage.prototype` so writes go through
// a Map-backed in-memory store; no app or test code subscribes to the `storage`
// event, so the dispatch is dead weight in the test runner.
installStorageShim()

// Global MSW server: every default endpoint handler is registered up front
// so tests that do not configure their own overrides get a predictable
// happy-path response for any request. Requests that fall through to a
// path with no handler fail the test loudly (`onUnhandledRequest: 'error'`)
// so new endpoints cannot ship without a matching default handler.
//
// This file is loaded ONLY by the ``unit`` project in
// ``vitest.config.ts``; the ``bench`` project loads
// ``./src/bench-setup.ts`` (no MSW, no React, no Motion). Keeping MSW out
// of the bench project matters because a second ``setupServer().listen()``
// across bench workers trips MSW's single global-interceptor invariant.
export const server = setupServer(...defaultHandlers)

beforeAll(() => {
  // The axios client attaches X-CSRF-Token on mutating requests by reading
  // the `csrf_token` cookie. Seed it here so every POST/PUT/PATCH/DELETE
  // test sends the header without having to log in first. Seeding in
  // `beforeAll` (not `beforeEach`) is deliberate: even with the
  // cookie shim, redoing the assignment for every test is wasted
  // work the test dispatcher does not require, and `beforeAll`
  // matches the contract (CSRF seed is one-time per worker).
  document.cookie = `csrf_token=${CSRF_SEED_VALUE}; path=/`
  server.listen({ onUnhandledRequest: 'error' })
})

afterEach(async () => {
  // The session probe is module-level state a 401 starts fire-and-forget, so
  // any suite that provokes one leaves it running into the next test. Drained
  // here rather than in the one suite that asserts on probe counts, because
  // the suite that leaks it is not the suite that notices.
  await _settleSessionProbeForTests()
  server.resetHandlers()
  // Clear any cookies a test wrote to the jar so state cannot leak across
  // tests in the same Vitest worker, then restore the global CSRF seed so
  // mutating-request tests still send `X-CSRF-Token` without re-seeding
  // through `document.cookie` (which would route through tough-cookie's
  // Promise wrapper, slowing the test).
  for (const name of Object.keys(cookieJar)) {
    Reflect.deleteProperty(cookieJar, name)
  }
  cookieJar['csrf_token'] = CSRF_SEED_VALUE
  resetCircuitBreaker()
  // The org-conversation store holds the unified transcript in module scope,
  // so it must be cleared between tests or a prior test's messages leak into
  // the next render.
  useOrgConversationStore.getState().resetAll()
  // The open-questions store holds the parked questions the chat page renders
  // (plus its refetch-coalescing flags) in module scope, so a prior test's
  // questions would otherwise appear in the next render.
  useOrgQuestionsStore.getState().reset()
  resetMessageIds()
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
// `MotionValue.start` and schedules its resolution through the next
// frame (rAF, polyfilled below as a queueMicrotask). Replacing
// `motion.*` with plain host elements and `AnimatePresence` with a
// passthrough removes the animation code path entirely, keeping
// per-test work minimal. Tests that assert on motion-specific
// behaviour can still opt out via their own
// `vi.mock('motion/react', ...)`.
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

// jsdom polyfills ``requestAnimationFrame`` with a shared ``setInterval``
// that only clears when every registered callback has fired; recharts's
// ``ZIndexPortal`` schedules rAF callbacks that keep re-scheduling, so
// the interval outlives the test. We replace rAF with a microtask-based
// shim: each callback queues a microtask, so an N-deep rAF chain
// (recharts animation, ~93 frames at 60Hz) unwinds entirely within the
// current test's await chain and never produces an event-loop-holding
// handle. ``cancelAnimationFrame`` flips a cancelled-flag the wrapper
// checks before invoking the callback, preserving the cancellation
// contract without a clearable handle.
//
// Why microtasks (not ``setTimeout(0)``): with macrotask scheduling,
// each frame in a recharts chain is a separate event-loop tick, so
// the active-handle detector observed Timeouts still in flight at
// ``afterEach`` time. Microtasks drain before any macrotask boundary
// (including ``await``), so the chain is guaranteed to complete in
// the same test phase that scheduled it.
//
// d3-timer (used by d3-force in ``pages/org/force-layout.ts``) binds
// ``setFrame`` to this shim at module load and relies on its wake()
// callback firing to clear its internal ``setInterval(poke)`` after
// ``simulation.stop()``. Microtask delivery is no slower than
// ``setTimeout(0)`` from d3's perspective; both fire on the next
// event-loop boundary the test awaits.
if (typeof window !== 'undefined') {
  const cancelled = new Set<number>()
  let nextRafId = 0
  window.requestAnimationFrame = (callback: FrameRequestCallback): number => {
    const id = ++nextRafId
    queueMicrotask(() => {
      if (cancelled.has(id)) {
        cancelled.delete(id)
        return
      }
      callback(performance.now())
    })
    return id
  }
  window.cancelAnimationFrame = (id: number): void => {
    cancelled.add(id)
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

// jsdom does not implement Element.scrollTo; the meta conversational
// surfaces call it in a requestAnimationFrame callback after appending a
// turn. Without a shim that rAF callback throws an unhandled error after
// the test completes. A no-op keeps the scroll-to-bottom behaviour inert
// under jsdom while real browsers use the native implementation.
if (
  typeof Element !== 'undefined' &&
  typeof Element.prototype.scrollTo !== 'function'
) {
  Element.prototype.scrollTo = () => {}
}

// Toast store schedules a `setTimeout` per auto-dismiss (success / info toasts
// with a real timer). Without a global teardown hook these timers survive the
// test boundary and the active-handle gate fails the test. `dismissAll()`
// clears both the pending handles and the toasts array in one idiomatic call;
// tests that need to inspect the toasts list after pending timers drain can
// instead call `cancelAllPending()` directly in their own teardown.
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

// Each teardown step below is independent of the others (no
// inter-store dependency), so the ordering is alphabetical-ish only
// for human readability. The invariant the active-handle gate
// requires is that every store that schedules a timer or attaches a
// listener registers ITS OWN teardown call in this block.
afterEach(() => {
  useToastStore.getState().dismissAll()
  // Setup-wizard store is backend-sourced (a pure API consumer, no client
  // persistence); reset its in-memory singleton so a test's wizard state does
  // not bleed into the next test in the same Vitest worker.
  useSetupWizardStore.getState().reset()
  // Org-chart-prefs store is backend-sourced (no client persistence); reset
  // its in-memory singleton state so toolbar toggles a test sets do not bleed
  // into the next test in the same Vitest worker.
  resetOrgChartPrefs()
  // Dashboard prefs store is backend-sourced; reset its in-memory singleton
  // so a test's toggles do not bleed into the next test in the same worker.
  resetDashboardPrefs()
  // Health store holds the shared /health snapshot the status pill and the
  // health dialog both render; reset it so a prior test's subsystem verdicts
  // do not bleed into the next in the same worker, and so a probe still in
  // flight cannot land on the next test's state.
  resetHealthStore()
  // Org-pulse store holds the subsystem phases and parked tasks the dashboard's
  // pulse panel reads; without this a test asserting the all-clear state
  // inherits whichever blockers an earlier test loaded.
  resetOrgPulseStore()
  // The provider-health revision counter lives in module scope, so a test
  // that rechecks leaves it advanced and the next test's health reads
  // silently drop themselves as stale.
  resetHealthRevision()
  // Providers store holds the fetched provider list; without this a test that
  // renders the Providers page inherits whichever providers an earlier test
  // loaded, which reads as correct whenever it happens to agree and is wrong
  // exactly for the tests that are about having none yet.
  resetProvidersStore()
  // Plan-forecast store holds a per-view forecast + request token; clear it so
  // a prior test's forecast does not bleed into the next in the same worker.
  usePlanForecastStore.getState().clear()
  // Plan-evaluation store holds a per-view verdict history + request token;
  // clear it so a prior test's verdicts do not bleed into the next.
  usePlanEvaluationStore.getState().clear()
  // Plan-comments store holds the current plan's thread + request token; reset
  // it so a prior test's comments do not bleed into the next in the same worker.
  usePlanCommentsStore.getState().reset()
  // Plans store holds the review inbox + selected plan + filter; reset it so a
  // prior test's plans do not bleed into the next in the same worker.
  usePlansStore.getState().reset()
  // The capability matrix is cached for the whole session and consumers
  // subscribe to it for live refreshes; clear both so a test inherits neither
  // an earlier test's matrix nor a setter belonging to an unmounted tree.
  resetCapabilitiesCache()
  // MCP-catalog ``setSearchQuery`` schedules a 200ms debounce
  // ``setTimeout``; clear any pending handle so it cannot outlive
  // the test and trip the active-handle gate.
  cancelPendingMcpCatalogSearch()
  // Theme store subscribes to a `prefers-reduced-motion` MediaQueryList
  // at factory time; detach the listener here so the active-handle
  // gate does not flag a forgotten subscription per test. Paired with
  // the ``reattach()`` in the ``beforeEach`` above.
  useThemeStore.getState().teardown()
})
