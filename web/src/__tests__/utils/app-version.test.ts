import fc from 'fast-check'
import { http, HttpResponse } from 'msw'
import { ensureFreshAppState } from '@/utils/app-version'
import { server } from '@/test-setup'

const STORAGE_KEY = 'synthorg:app_build_id'

/**
 * Vite's `define` substitutes `import.meta.env.VITE_APP_BUILD_ID` at
 * build time. In tests (Vitest reuses the Vite pipeline) it resolves
 * to the value baked in `vite.config.ts` -- which reads package.json.
 * Read the substituted value here so tests adapt to whatever is baked.
 */
const CURRENT_BUILD_ID: string = import.meta.env.VITE_APP_BUILD_ID ?? 'dev'

/**
 * Per-test logout-call recorder. The global MSW server in
 * `test-setup.tsx` already registers a default `POST /api/v1/auth/logout`
 * handler (via `authHandlers`); each test overrides it here with
 * `server.use(...)` so we can observe the call site without standing up
 * a second `setupServer` (which conflicted with the global interceptor
 * chain and caused double-invocation under `msw/node`).
 */
const logoutCalls: Array<{ credentials?: RequestCredentials }> = []

function installLogoutRecorder(
  responder: (() => Response) | null = null,
): void {
  server.use(
    http.post('/api/v1/auth/logout', ({ request }) => {
      logoutCalls.push({ credentials: request.credentials })
      return responder ? responder() : new HttpResponse(null, { status: 204 })
    }),
  )
}

/**
 * Sentinel error thrown from the stubbed ``window.location.reload`` to
 * deterministically short-circuit ``ensureFreshAppState`` after it
 * reaches the reload boundary.  Without this, the production code
 * ``await new Promise(() => {})`` would hang the test forever -- the
 * previous `Promise.race` + 50 ms timer approach was flaky under
 * CI load (the assertions only held if logout + clear + reload all
 * landed within the timeout).
 */
class ReloadSentinel extends Error {
  constructor() {
    super('ReloadSentinel')
    this.name = 'ReloadSentinel'
  }
}

describe('ensureFreshAppState', () => {
  let reloadSpy: ReturnType<typeof vi.fn>
  // Snapshot the real ``window.location`` descriptor once -- each
  // test replaces it with a mock via ``Object.defineProperty``, and
  // ``vi.restoreAllMocks()`` does NOT undo that, so we must restore
  // it ourselves in ``afterEach``.  Without this, the mutated
  // Location stub leaks into unrelated tests that run later in the
  // same file (and into the real ``location`` global if the Vitest
  // environment is reused).
  const originalLocationDescriptor = Object.getOwnPropertyDescriptor(
    window,
    'location',
  )

  afterAll(() => {
    if (originalLocationDescriptor) {
      Object.defineProperty(window, 'location', originalLocationDescriptor)
    }
  })

  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
    logoutCalls.length = 0
    installLogoutRecorder()
    reloadSpy = vi.fn(() => {
      throw new ReloadSentinel()
    })
    // Preserve href so MSW can resolve `/api/v1/auth/logout` against
    // a valid base URL -- jsdom's default `about:blank` breaks fetch
    // with "Invalid URL" on relative paths, which silently swallows
    // the logout request before MSW sees it.
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: {
        href: 'http://localhost/',
        origin: 'http://localhost',
        reload: reloadSpy,
      },
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
    if (originalLocationDescriptor) {
      Object.defineProperty(window, 'location', originalLocationDescriptor)
    }
    // Global test-setup.tsx afterEach runs server.resetHandlers() after
    // this, so we only need to clean up the location mock here.
  })

  /**
   * Invoke ``ensureFreshAppState`` and swallow the ``ReloadSentinel``
   * thrown from the stubbed ``reload``.  Anything else propagates so
   * real failures still mark the test as failed.
   */
  async function runUntilReload(): Promise<void> {
    try {
      await ensureFreshAppState()
    } catch (err) {
      if (!(err instanceof ReloadSentinel)) throw err
    }
  }

  it('stamps the build id on first load and does not call logout or reload', async () => {
    await ensureFreshAppState()
    expect(localStorage.getItem(STORAGE_KEY)).toBe(CURRENT_BUILD_ID)
    expect(logoutCalls).toHaveLength(0)
    expect(reloadSpy).not.toHaveBeenCalled()
  })

  it('is a no-op when the stored id matches the current build id', async () => {
    localStorage.setItem(STORAGE_KEY, CURRENT_BUILD_ID)
    localStorage.setItem('other-key', 'keep-me')
    await ensureFreshAppState()
    expect(logoutCalls).toHaveLength(0)
    expect(reloadSpy).not.toHaveBeenCalled()
    expect(localStorage.getItem('other-key')).toBe('keep-me')
  })

  it('on mismatch: calls POST /auth/logout, clears storage, and stamps the new id', async () => {
    localStorage.setItem(STORAGE_KEY, 'some-old-build')
    localStorage.setItem('theme', 'dark')
    sessionStorage.setItem('tmp', 'value')

    await runUntilReload()

    expect(logoutCalls).toHaveLength(1)
    expect(logoutCalls[0]!.credentials).toBe('include')
    expect(localStorage.getItem('theme')).toBeNull()
    expect(sessionStorage.getItem('tmp')).toBeNull()
    expect(localStorage.getItem(STORAGE_KEY)).toBe(CURRENT_BUILD_ID)
    expect(reloadSpy).toHaveBeenCalledTimes(1)
  })

  it('continues on logout fetch failure (best-effort) and still clears + reloads', async () => {
    localStorage.setItem(STORAGE_KEY, 'some-old-build')
    installLogoutRecorder(() => HttpResponse.error())

    await runUntilReload()

    expect(localStorage.getItem(STORAGE_KEY)).toBe(CURRENT_BUILD_ID)
    expect(reloadSpy).toHaveBeenCalledTimes(1)
  })

  it('returns silently when localStorage.getItem throws (private mode)', async () => {
    const getItemSpy = vi
      .spyOn(Storage.prototype, 'getItem')
      .mockImplementation(() => {
        throw new DOMException('blocked', 'SecurityError')
      })

    await ensureFreshAppState()
    expect(logoutCalls).toHaveLength(0)
    expect(reloadSpy).not.toHaveBeenCalled()
    getItemSpy.mockRestore()
  })

  it('skips reload when final setItem fails (avoids infinite loop)', async () => {
    localStorage.setItem(STORAGE_KEY, 'some-old-build')
    // eslint-disable-next-line @typescript-eslint/unbound-method -- saved to restore Storage.prototype.setItem after the test; never invoked unbound
    const originalSetItem = Storage.prototype.setItem
    const setItemSpy = vi
      .spyOn(Storage.prototype, 'setItem')
      .mockImplementation(function (
        this: Storage,
        key: string,
        value: string,
      ) {
        // Let the seed write succeed; fail the post-clear stamp.
        if (key === STORAGE_KEY && value === CURRENT_BUILD_ID) {
          throw new DOMException('quota', 'QuotaExceededError')
        }
        originalSetItem.call(this, key, value)
      })

    await ensureFreshAppState()
    expect(reloadSpy).not.toHaveBeenCalled()
    setItemSpy.mockRestore()
  })

  it('exercises the mismatch path for any arbitrary non-matching stored id', async () => {
    // Property: for every string that is NOT the current build id,
    // ``ensureFreshAppState`` must (1) POST logout exactly once,
    // (2) clear ``localStorage``/``sessionStorage`` except the new
    // stamp, (3) stamp the current build id, and (4) hit the reload
    // boundary (``reloadSpy`` throws ``ReloadSentinel``).
    //
    // Low ``numRuns`` keeps unit-suite wall time tight; deep fuzzing
    // runs via the ``HYPOTHESIS_PROFILE``-equivalent dev profile are
    // covered by CI's broader property-test runs.
    await fc.assert(
      fc.asyncProperty(
        fc.string().filter((s) => s !== CURRENT_BUILD_ID),
        async (storedId) => {
          localStorage.clear()
          sessionStorage.clear()
          logoutCalls.length = 0
          reloadSpy.mockClear()
          localStorage.setItem(STORAGE_KEY, storedId)
          localStorage.setItem('theme', 'keep-until-cleared')
          sessionStorage.setItem('tmp', 'keep-until-cleared')

          await runUntilReload()

          expect(logoutCalls).toHaveLength(1)
          expect(localStorage.getItem('theme')).toBeNull()
          expect(sessionStorage.getItem('tmp')).toBeNull()
          expect(localStorage.getItem(STORAGE_KEY)).toBe(CURRENT_BUILD_ID)
          expect(reloadSpy).toHaveBeenCalledTimes(1)
        },
      ),
      { numRuns: 25 },
    )
  })
})
