# Web HTTP Adapter

> **Spec topic**: web dashboard HTTP layer.

## Decision

The dashboard uses `axios` with the default `XMLHttpRequest` adapter
in production and in tests. MSW 2.x intercepts requests in the test
environment via its XHR interceptor.

The async-leak count is bounded by a CI ceiling read from
`.github/ci/web-async-leaks.max` rather than driven to zero. The
remaining structural leaks live inside MSW's interceptor stack and
inside axios's internal Promise chain; eliminating them requires
replacing MSW's matching layer wholesale, which is tracked separately
under #1468 and is rejected as load-bearing for Storybook
(`msw-storybook-addon`) and for the typed-handler helpers
(`successFor<typeof endpoint>`, `paginatedFor<typeof endpoint>`,
per-domain `buildEntity()` builders) that give compile-time drift
detection against the endpoint modules.

## What "async leaks" actually are

Vitest's `--detect-async-leaks` flag installs a Node
`async_hooks.createHook` that tracks every Promise created during a
test file (and every Promise triggered from one). At end of file it
collects Promises whose `promiseResolve` hook never fired (i.e. that
never settled from Node's point of view). Those are reported as `N ×
PROMISE leaking in <file>`.

The distinction is important: **settled Promises do not leak**, even
if no caller ever awaited them. A leak is a Promise that stays
pending forever. The stacks Vitest prints for each leak are the
Promise's *creation* stack, not the stack at detection time.

## Sources of remaining leaks

Three categories survive after the in-tree shims and synchronous
interceptor option:

1. **MSW cookie store**: MSW maintains its own cookie jar separate
   from `document.cookie`; `CookieStore.getCookies`
   (`node_modules/msw/lib/core/utils/cookieStore.mjs`) calls
   `tough-cookie.getCookiesSync`, which internally allocates a
   Promise via `createPromiseCallback` even in the sync variant.
   Unreachable from user-space.
2. **MSW XHR interceptor**: each `xhr.send()` schedules a
   `queueMicrotask(() => onRequest?.call(...).finally(...))` plus a
   sibling that clones the `fetchRequest`. The microtask settles, but
   the finalizer chain runs an emitter that itself awaits
   `emitAsPromise`, which through `InterceptorSource.queue` binds to
   MSW's internal async frame. When the test body returns, the frame
   is still pending from `async_hooks`'s POV.
3. **axios internal Promise chain**: `axios/lib/core/Axios.js:196`
   (`promise = promise.then(chain[i++], chain[i++])`) builds a
   Promise chain per request. The outermost `.then()` is `init`'d
   during the test but only `promiseResolve`s when the full MSW
   interceptor chain has fully settled, which, per category 2, it
   has not.

## What's in the codebase

1. **Cookie shim** -- `web/src/cookie-shim.ts`, installed from
   `web/src/test-setup.tsx` and `web/src/bench-setup.ts`. Replaces
   `Document.prototype`'s `cookie` descriptor with a synchronous
   in-memory jar so jsdom's tough-cookie Promise allocation is
   bypassed. Delete-style writes (`Max-Age=0` or a past `Expires=`)
   remove the entry so `utils/app-version.ts::clearClientVisibleCookies`
   behaves like the browser, and prototype keys (`__proto__`,
   `constructor`) are rejected as defense-in-depth.
2. **Storage shim** -- `web/src/storage-shim.ts`, installed from
   `web/src/test-setup.tsx`. Patches `Storage.prototype` methods to
   bypass jsdom's `_dispatchStorageEvent` `setTimeout(0)` path; no
   app or test code subscribes to the `storage` event, so the
   dispatch is dead weight in the test runner. Backed by an
   instance-keyed `WeakMap` so `localStorage instanceof Storage`
   stays true and `vi.spyOn(Storage.prototype, 'setItem')` continues
   to intercept.
3. **Synchronous request interceptor** -- `web/src/api/client.ts`
   passes `{ synchronous: true }` to
   `apiClient.interceptors.request.use(...)` so axios skips the
   `.then(chain[i++], ...)` loop at `Axios.js:196` when no async
   interceptor is registered. The CSRF interceptor is itself
   synchronous; the annotation tells axios to take the fast path.
4. **CI ceiling** -- `.github/ci/web-async-leaks.max` holds the
   current ceiling. The `Dashboard Test` job runs vitest under
   `NO_COLOR=1`, anchors the parser to the full `Leaks N leaks`
   summary line, and fails closed if the line is absent or
   malformed.

## Behavioral audit (axios fetch adapter)

`axios.defaults.adapter` defaults to the XHR adapter in browsers and
ships with a `fetch` adapter alternative. The fetch adapter is not
adopted because:

| Dimension | axios XHR (current prod) | axios fetch (evaluated) | Verdict |
|---|---|---|---|
| `responseType: 'blob'` | Works (`src/api/endpoints/artifacts.ts:30`) | Supported in axios 1.15+ but the conversion path differs; untested against `downloadArtifactContent` | No change needed |
| `responseType: 'text'` | Works (`artifacts.ts:38`) | Same as above | No change needed |
| `withCredentials: true` | Works (`src/api/client.ts:52`); cookies attached automatically | `credentials: 'include'` equivalent; cookie handling differs between browsers and jsdom but is identical in prod browsers | No behavioral delta in prod |
| `timeout: 30_000` | `xhr.timeout` (wire time) | `AbortSignal.timeout` (wall-clock from request start) | Prod-browser behavior equivalent |
| 429 retry + `Retry-After` + `_rateLimitRetries` | Works; config mutation survives axios's recursive `apiClient.request(retryConfig)` | Fetch adapter clones config differently; would need `WeakMap<InternalAxiosRequestConfig, number>` refactor to be safe | Non-trivial refactor required |
| CSRF interceptor (`client.ts:110-119`) | Works | Same; headers attachment is adapter-agnostic | No change |
| 401 handler (`client.ts:128-140`) | Works | Same | No change |
| `ApiResponse` / `PaginatedResponse` envelope unwrap | Works | Same; `response.data` shape is adapter-agnostic | No change |
| `signal: AbortController` | Works | Works | No change |
| SSE / streaming (`src/api/endpoints/providers.ts:162-237`) | Already uses native `fetch` directly, bypassing axios | Unchanged | Adapter-agnostic |
| `onUploadProgress` / `onDownloadProgress` | Not used anywhere | Fetch adapter does not support upload progress | N/A |
| `FormData` / `File` / `Blob` bodies | Not used | Both adapters support natively | N/A |
| `paramsSerializer`, `maxContentLength`, `decompress` | Not configured | Same | No change |
| `err.request` introspection | Not used in app or tests | Fetch adapter does not set `response.request` | No change |
| Bundle size | Tree-shaken into `vendor-state` chunk (~5KB gzipped contribution) | Similar size | Not a forcing factor |
| Browser support | All modern (no IE11 constraint) | All modern | Equivalent |
| MSW interception | Via `@mswjs/interceptors/XMLHttpRequest` | Via `@mswjs/interceptors/fetch`, which produces materially more Promise chains than the XHR variant | XHR path strictly better for leak count |

There is no operational reason to move production to the fetch
adapter. The fetch adapter would require the `_rateLimitRetries`
WeakMap refactor to keep the retry path safe, and MSW-leak behavior
in tests would get worse, not better. XHR is the right choice for
this stack.

## Approaches that don't work

These were measured and rejected; the reasons are load-bearing
because the same temptations recur:

- **Replace jsdom with happy-dom** -- happy-dom does not touch MSW
  at all and introduces a separate happy-dom-specific leak category
  via `FetchBodyUtility.toReadableStream`. Net: worse.
- **Switch axios to the fetch adapter** -- moves the structural
  floor onto MSW's *fetch* interceptor path, which generates more
  Promise chains than its XHR interceptor. Net: worse.
- **Sync `globalThis.queueMicrotask`** -- replacing the global with
  a sync wrapper so MSW's XHR interceptor dispatch runs in-line just
  creates more Promise identity shifts for the tracker to flag. Net:
  worse.
- **Custom `axios.defaults.adapter` that dispatches directly to
  MSW's `handler.run()`** -- bypasses `@mswjs/interceptors` for XHR
  but `handler.run` itself reads cookies via
  `getAllRequestCookies` per call, and MSW's
  `HttpHandler.cloneRequestOrGetFromCache` plus the
  `ClientRequest` interceptor (still installed by `setupServer`)
  add new leaks of their own. Net: worse.
- **Monkey-patch `XMLHttpRequest.prototype.send` to abort live
  XHRs in `afterEach`** -- the leaks are from *completed* XHRs;
  draining the live set does not reach them. Net: zero.
- **Microtask / `setImmediate` drain in `afterEach`** -- leaks
  survive `Promise.resolve(setImmediate)` collection. Net: zero.

The only path to zero leaks identified by the investigation is
replacing MSW 2.x with a mock layer that does not use
`@mswjs/interceptors` (e.g. `nock`, which intercepts at
`http.request`, or plain axios adapter mocks). Tracked under #1468.

## References

- #1466: "eliminate async leaks" research issue.
- #1467: "evaluate switching axios XHR adapter to fetch" (closed:
  no behavioral or operational reason to switch).
- #1468: research candidate for replacing MSW's matching layer.
- `web/CLAUDE.md`: MSW handlers contract and test teardown rules.
- `docs/reference/web-zustand-stores.md`: per-store teardown
  catalog and the async-leak ceiling audit trail.
