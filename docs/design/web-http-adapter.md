# Web HTTP Adapter

> **Spec topic**: web dashboard HTTP layer.

## Decision

The dashboard uses `axios` with the default `XMLHttpRequest` adapter
in production and in tests. MSW 2.x intercepts requests in the test
environment via its XHR interceptor.

Test correctness with respect to forgotten timers, sockets, and
listeners is enforced at runtime by the per-test active-handle
detector documented in
[web-active-handle-detection.md](./web-active-handle-detection.md).
That detector replaces the previous `vitest --detect-async-leaks`
ceiling: instead of counting Promises (most of which originate in
`node_modules/`), it diffs the live set of Node `async_hooks`
resources around every test and fails the run when a tracked handle
attributable to `web/src/` survives `afterEach`.

## What the active-handle gate enforces here

The HTTP adapter is one of the surfaces the previous `--detect-async-leaks`
count exercised most heavily, because every MSW-intercepted request
allocates Promises inside `@mswjs/interceptors`, `axios`'s internal
chain, and (in jsdom) `tough-cookie`'s sync-shaped Promise wrapper.
Those Promises always *settle* during the test; they are not real
leaks. The active-handle detector ignores them entirely because
Promises are not event-loop-holding resources. What it does enforce
is: a request that schedules a timer (e.g. a retry backoff via
`setTimeout`) MUST cancel that timer before its test ends, or the
test fails.

## What's in the codebase

1. **Cookie shim**: `web/src/cookie-shim.ts`, installed from
   `web/src/test-setup.tsx` and `web/src/bench-setup.ts`. Replaces
   `Document.prototype`'s `cookie` descriptor with a synchronous
   in-memory jar so jsdom's tough-cookie path does not allocate
   per-write Promises during tests. Test-speed optimisation;
   prototype-pollution defence is the primary security purpose.
2. **Storage shim**: `web/src/storage-shim.ts`, installed from
   `web/src/test-setup.tsx`. Patches `Storage.prototype` methods to
   bypass jsdom's `_dispatchStorageEvent` `setTimeout(0)` path; no
   app or test code subscribes to the `storage` event, so the
   dispatch is dead weight in the test runner. Backed by an
   instance-keyed `WeakMap` so `localStorage instanceof Storage`
   stays true and `vi.spyOn(Storage.prototype, 'setItem')` continues
   to intercept.
3. **Synchronous request interceptor**: `web/src/api/client.ts`
   passes `{ synchronous: true }` to
   `apiClient.interceptors.request.use(...)` so axios skips the
   `.then(chain[i++], ...)` loop at `Axios.js:196` when no async
   interceptor is registered. The CSRF interceptor is itself
   synchronous; the annotation tells axios to take the fast path.
   No behavioural change; pure perf hint.

## Behavioural audit (axios fetch adapter)

`axios.defaults.adapter` defaults to the XHR adapter in browsers and
ships with a `fetch` adapter alternative. The fetch adapter is not
adopted because:

| Dimension | axios XHR (current prod) | axios fetch (evaluated) | Verdict |
|---|---|---|---|
| `responseType: 'blob'` | Works (`src/api/endpoints/artifacts.ts:30`) | Supported in axios 1.15+ but the conversion path differs; untested against `downloadArtifactContent` | No change needed |
| `responseType: 'text'` | Works (`artifacts.ts:38`) | Same as above | No change needed |
| `withCredentials: true` | Works (`src/api/client.ts:52`); cookies attached automatically | `credentials: 'include'` equivalent; cookie handling differs between browsers and jsdom but is identical in prod browsers | No behavioural delta in prod |
| `timeout: 30_000` | `xhr.timeout` (wire time) | `AbortSignal.timeout` (wall-clock from request start) | Prod-browser behaviour equivalent |
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
| MSW interception | Via `@mswjs/interceptors/XMLHttpRequest` | Via `@mswjs/interceptors/fetch` | Functionally equivalent for tests under the active-handle gate |

There is no operational reason to move production to the fetch
adapter. The fetch adapter would require the `_rateLimitRetries`
WeakMap refactor to keep the retry path safe, with no countervailing
production benefit.

## References

- [web-active-handle-detection.md](./web-active-handle-detection.md):
  what the per-test handle gate catches, allowlist policy, and
  telemetry shape.
- `web/CLAUDE.md`: MSW handlers contract and test teardown rules.
- `docs/reference/web-zustand-stores.md`: per-store teardown
  catalog.
