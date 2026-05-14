# Web Active-Handle Detection

> **Spec topic**: per-test resource-leak detection in the web dashboard.

## Decision

Forgotten event-loop-holding resources (timers, sockets, pipes, file
watchers, child processes, signal handlers, ...) in any unit test are
treated as hard failures. The detector is a vitest `setupFile`
(`web/test-infra/active-handle-tracker.ts`) that hooks Node's
`async_hooks`, snapshots the live resource set in a global
`beforeEach`, and diffs it in a global `afterEach`. Survivors whose
creation stack reaches `web/src/` cause the tracker to throw inside
`afterEach`, surfacing the failure on the originating test. A
companion main-process reporter
(`web/test-infra/active-handle-reporter.ts`) aggregates per-worker
NDJSON records and prints a summary at end of run; in CI it also
emits a `handle-telemetry.json` artifact for drift tracking.

This replaces the prior `--detect-async-leaks` ceiling gate
(`.github/ci/web-async-leaks.max`). That ceiling counted Promises,
~95% of which originated inside `node_modules/` (MSW interceptors,
axios's response chain, tough-cookie wrappers); the resulting number
could not distinguish a real leak from MSW's structural floor, so it
required a manually-curated buffer and was ratcheted up over time.
The new detector counts resources that actually hold the event loop
open, attributed per-test, with zero tolerance and no buffer.

## What it catches

Every `init` callback for a resource type in `TRACKED_TYPES` (see
`active-handle-tracker.ts`) captures the creation stack. At
`afterEach`, the detector yields enough `setImmediate` cycles for
Node's pending `destroy` callbacks to fire (the standard teardown
pattern is `clearTimeout` -> destroy on a later loop pass), then
diffs against the snapshot. Survivors are classified:

- If `findUserFrame(stack)` returns a `web/src/` frame outside the
  test harness (`node_modules/`, `vitest`, `vite`, `test-infra/`,
  `node:internal/...`), the resource is attributed to that frame
  and either logged or thrown, depending on `ACTIVE_HANDLE_MODE`.
- If no such frame exists, the resource is structural (worker pool,
  jsdom internals, vitest itself). It is ignored.

Tracked types include `Timeout`, `Immediate`, `TCPWRAP`,
`TCPSERVERWRAP`, `TCPCONNECTWRAP`, `UDPWRAP`, `UDPSENDWRAP`,
`PIPEWRAP`, `PIPECONNECTWRAP`, `TLSWRAP`, `FSEVENTWRAP`,
`FSREQCALLBACK`, `HTTPCLIENTREQUEST`, `HTTPINCOMINGMESSAGE`,
`HTTP2SESSION`, `HTTP2STREAM`, `HTTP2PING`, `HTTP2SETTINGS`, `ZLIB`,
`CHILDPROCESS`, `SIGNALWRAP`, `STATWATCHER`, `WRITEWRAP`,
`SHUTDOWNWRAP`, and `MESSAGEPORT`: the full enumeration lives at
`TRACKED_TYPES_LIST` in `web/test-infra/active-handle-tracker.ts` and
is the single source of truth. `PROMISE`, `Microtask`, and DNS
request types are deliberately excluded as non-event-loop-holding
(they always settle).

Typical bug shapes the gate fires on:

- `setTimeout` / `setInterval` scheduled in a hook or handler and
  never cleared.
- A socket / TCP connection opened in a test and not closed.
- An `addEventListener` or `process.on(...)` registered without a
  paired remove / off call.
- A Zustand `subscribe` returned-unsubscriber that the test forgets
  to invoke.

These are the bug shapes that map to production failure modes
(memory growth, dangling handlers, slow shutdown).

## What it does NOT catch (intentional non-goals)

- **Unsettled Promises.** A `.then()` chain inside MSW that never
  reaches its terminator is invisible to the detector. This is by
  design: such Promises are not handles, they do not hold the event
  loop, they do not leak memory in any meaningful sense, and they
  were the root cause of the previous gate's noise floor.
- **Memory leaks not backed by a handle.** A large array kept alive
  by a forgotten closure is a memory leak but creates no handle;
  use a separate memory-profiling pass for those cases.
- **Synchronous fences.** A test that mutates global state and never
  cleans up is a state leak, caught by the existing test-setup
  teardown contract (`web/CLAUDE.md` -> Test teardown).
- **Async work that crosses files.** The diff is per-test. A
  resource created in file A's test and destroyed in file B's test
  is treated as a leak in A. This is intentional: tests must be
  hermetic.

## Modes and configuration

`ACTIVE_HANDLE_MODE` environment variable:

- `fail` (default): throw at `afterEach` on any unallowlisted
  user-attributable leak. The throw fails the test through vitest's
  normal pipeline. CI runs in this mode unconditionally.
- `log`: record leaks to NDJSON but never fail the test. Use when
  triaging a new leak source or working on test infrastructure where
  the failure noise would obscure the real signal.

`ACTIVE_HANDLE_LOG_DIR` environment variable: overrides the directory
the tracker writes its per-worker NDJSON to (default
`<cwd>/.test-tmp`). Used by the regression suite to isolate a child
vitest run's records from the parent's reporter.

## Allowlist criteria

The allowlist (`web/test-infra/active-handle-allowlist.ts`) is
intentionally empty. Adding an entry is a deliberate audit step:

1. Confirm the resource is created by code outside our control AND
   cannot be released by the call site. "Tests pass with this entry
   removed" is sufficient evidence the entry is unnecessary; remove
   it.
2. Write the `reason` explaining the structural floor and citing a
   follow-up issue if the upstream owner could fix it.
3. Make the `framePattern` regex tight enough that it cannot
   silently swallow unrelated leaks of the same type. A pattern
   like `/.*/` is never acceptable.

If a leak has any path to being fixed at source (a missing
`clearTimeout`, a missing unsubscribe, a missing `removeEventListener`)
the fix belongs in the test or production code, not in the
allowlist.

## Telemetry

When `CI=true`, the main-process reporter writes
`web/.test-tmp/handle-telemetry.json` summarising the run:

```json
{
  "schemaVersion": 1,
  "generatedAt": "2026-05-14T15:54:00.000Z",
  "mode": "fail",
  "totalLeaks": 0,
  "unallowedLeaks": 0,
  "byType": {},
  "byTest": [],
  "records": []
}
```

The CI workflow uploads this file via `actions/upload-artifact`
(`.github/workflows/ci.yml` -> Dashboard Test). The artifact is
retained for 30 days. Downstream dashboards / drift charts can be
wired against the same schema without re-instrumenting tests.

`schemaVersion` is bumped on breaking JSON shape changes; consumers
should reject other versions rather than guess.

## End-to-end regression test

`web/src/__tests__/_infra/active-handle-reporter.test.ts` covers
both layers:

1. Pure-function unit tests for `findUserFrame`, `isUserFrame`, and
   `matchAllowlist`, locking in the classification rules.
2. A subprocess test that spawns a child `vitest run` against a
   deliberate-leak fixture
   (`web/src/__tests__/_infra/active-handle-reporter.fixture.ts` +
   `leak-helpers.ts`) and asserts every fixture test fails with the
   tracker's branded error naming the resource type and the
   originating user frame.

The fixture lives in `src/__tests__/_infra/` with a `.fixture.ts`
extension so the unit project's `*.test.{ts,tsx}` glob does not pick
it up; it only runs under the dedicated config
(`web/test-infra/active-handle-reporter.fixture.config.ts`). If the
tracker ever regresses to silently pass a real leak, the parent test
fails.

## ESLint companion rules

`@typescript-eslint/no-floating-promises` and
`@typescript-eslint/no-misused-promises` are enabled at error level
in `web/eslint.config.js` to catch the most common syntactic shapes
that lead to forgotten async work at edit time. `no-misused-promises`
runs with `checksVoidReturn: { attributes: false }` (the documented
React preset) so async JSX event handlers are not flagged; React
19's global error handler covers their rejection path.

The runtime detector and the ESLint rules are complementary: the
former catches anything that produces a handle, the latter catches
the literal "I forgot to await this" anti-pattern before it ever
runs.

## References

- `web/test-infra/active-handle-tracker.ts`: worker-side
  instrumentation (the actual hook + diff).
- `web/test-infra/active-handle-reporter.ts`: main-process Vitest
  reporter (aggregation + summary + telemetry).
- `web/test-infra/active-handle-allowlist.ts`: explicit allowlist
  (currently empty).
- [web-http-adapter.md](./web-http-adapter.md): what the HTTP
  adapter does to keep itself leak-free under this gate.
