/**
 * Per-test active-handle leak detector.
 *
 * Loaded as a vitest `setupFile` for the unit project; runs inside
 * every worker process. Hooks Node's `async_hooks` to record creation
 * of event-loop-holding resources (timers, sockets, pipes, file
 * watchers, child processes, ...), snapshots the live set before each
 * test, and diffs after.
 *
 * Survivors whose creation stack roots in `web/src/` and that are not
 * matched by an entry in `./active-handle-allowlist.ts` are recorded
 * to a per-worker NDJSON file (`web/.test-tmp/handle-leaks-<pid>.ndjson`),
 * picked up by the main-process reporter at end of run.
 *
 * Mode is controlled by `process.env.ACTIVE_HANDLE_MODE`:
 *   - `fail` (default): throw at `afterEach` if the test leaked a
 *     tracked handle that isn't allowlisted.
 *   - `log`: record leaks; never fail the test. Use when triaging
 *     a new leak source without breaking the suite.
 *
 * Why active handles, not Promises: `--detect-async-leaks` counts every
 * settled Promise that crossed a microtask, including library-internal
 * chains (MSW, axios, tough-cookie) that map to zero production
 * failure modes. Active handles (the things actually holding the
 * event loop open) map 1:1 to forgotten timers / sockets / listeners,
 * the bug classes we actually ship.
 */

import { createHook } from 'node:async_hooks'
import { appendFileSync, mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { setImmediate as setImmediateP } from 'node:timers/promises'

import { afterEach, beforeEach } from 'vitest'

import { ALLOWLIST, type AllowlistEntry } from './active-handle-allowlist'
import {
  TRACKED_TYPES_LIST,
  resolveLeakLogDir,
  type LeakAttribution,
  type LeakMode,
  type LeakRecord,
  type LeakType,
} from './active-handle-shared'

export type {
  LeakAttribution,
  LeakMode,
  LeakRecord,
  LeakType,
} from './active-handle-shared'
export { TRACKED_TYPES_LIST } from './active-handle-shared'

const TRACKED_TYPES: ReadonlySet<LeakType> = new Set(TRACKED_TYPES_LIST)

interface HandleRecord {
  type: LeakType
  // V8 formats Error.stack lazily on first read; storing the Error
  // and deferring the .stack access until a leak is actually detected
  // keeps the async_hooks init callback cheap enough not to perturb
  // microtask timing in fake-timer-driven test loops.
  error: Error
  createdAtMs: number
}

const liveHandles = new Map<number, HandleRecord>()
let snapshotIds: Set<number> | null = null

const mode: LeakMode =
  process.env['ACTIVE_HANDLE_MODE'] === 'log' ? 'log' : 'fail'

/**
 * Frame substrings we never attribute a leak to. These are the test
 * harness itself: flagging a stack rooted here would be a false
 * positive (the resource was created by the harness on the user's
 * behalf, e.g. vitest scheduling its own internal Timeout).
 */
export const HARNESS_FRAME_PATTERNS: readonly RegExp[] = [
  /[\\/]node_modules[\\/]/,
  /[\\/]web[\\/]test-infra[\\/]/,
  /[\\/]vitest[\\/]/,
  /[\\/]vite[\\/]/,
  /[\\/]@vitest[\\/]/,
  /^\s*at\s+node:/,
  /^\s*at\s+internal[\\/]/,
]

export function isUserFrame(frame: string): boolean {
  if (!/[\\/]web[\\/]src[\\/]/.test(frame)) return false
  for (const pattern of HARNESS_FRAME_PATTERNS) {
    if (pattern.test(frame)) return false
  }
  return true
}

/**
 * Walk a captured stack and return the first frame that resolves to
 * a `web/src/` file outside the harness. Returns `null` for stacks
 * that never reach user code (purely structural creates).
 */
export function findUserFrame(stack: string): string | null {
  const lines = stack.split('\n')
  for (const raw of lines) {
    const line = raw.trim()
    if (!line.startsWith('at ')) continue
    if (isUserFrame(line)) return line
  }
  return null
}

export function matchAllowlist(
  type: LeakType,
  stack: string,
  allowlist: readonly AllowlistEntry[] = ALLOWLIST,
): AllowlistEntry | null {
  for (const entry of allowlist) {
    if (entry.type !== type) continue
    if (entry.framePattern.test(stack)) return entry
  }
  return null
}

/**
 * Maximum drain iterations the ``afterEach`` hook runs while waiting for
 * Node's ``async_hooks`` ``destroy`` callbacks to settle. Exported so
 * tests can pin the value (raising it would silently change the false-
 * positive boundary for legitimate chained-timer use cases).
 */
export const MAX_DRAIN_ITERATIONS = 8

const hook = createHook({
  init(asyncId, type) {
    // Node's async_hooks signature delivers ``type`` as ``string``;
    // the ``has`` check narrows membership to ``LeakType`` so the
    // downstream cast is sound. TypeScript cannot narrow through
    // ``Set.has``, hence the explicit casts on both lines.
    if (!(TRACKED_TYPES as ReadonlySet<string>).has(type)) return
    // Construct the Error WITHOUT reading ``.stack``: V8 captures
    // raw frame pointers eagerly but defers string formatting until
    // first read, so the hot path stays free of the per-init
    // formatting cost. ``.stack`` is materialised in ``afterEach``
    // only for handles that actually leaked.
    liveHandles.set(asyncId, {
      type: type as LeakType,
      error: new Error(),
      createdAtMs: performance.now(),
    })
  },
  destroy(asyncId) {
    liveHandles.delete(asyncId)
  },
})
hook.enable()

// ``ACTIVE_HANDLE_LOG_DIR`` lets a parent process (e.g. the
// regression test in ``src/__tests__/_infra/active-handle-reporter.test.ts``)
// redirect a child vitest run's NDJSON output to an isolated dir so
// the parent's main-process reporter does not slurp the child's leak
// records. Validation enforces that the redirect stays inside the
// project tree.
const LEAK_LOG_DIR = resolveLeakLogDir(process.env['ACTIVE_HANDLE_LOG_DIR'])
const LEAK_LOG_FILE = join(
  LEAK_LOG_DIR,
  `handle-leaks-${String(process.pid)}.ndjson`,
)
let logDirReady = false

function ensureLogDir(): void {
  if (logDirReady) return
  try {
    // eslint-disable-next-line security/detect-non-literal-fs-filename -- self-managed cache dir
    mkdirSync(LEAK_LOG_DIR, { recursive: true })
  } catch {
    // Best-effort. If the directory cannot be created, individual
    // append calls below will throw and the leak record will be lost
    // for that worker, preferable to silently disabling tracking.
  }
  logDirReady = true
}

function recordLeak(leak: LeakRecord): void {
  ensureLogDir()
  try {
    // eslint-disable-next-line security/detect-non-literal-fs-filename -- self-managed cache dir
    appendFileSync(LEAK_LOG_FILE, `${JSON.stringify(leak)}\n`)
  } catch {
    // If the append fails (disk full, perms) we still want the test
    // to surface the issue via stderr below. Swallowing the IO error
    // keeps the test runner usable even when the leak log is broken.
  }
}

interface VitestTaskLike {
  name: string
  file?: { name?: string; filepath?: string }
}

/**
 * Best-effort lookup of the active test's name + file. vitest exposes
 * the running task through `globalThis.__vitest_worker__` in worker
 * threads; we read it defensively because the field is not part of
 * the public API and may change shape between versions. When the
 * lookup fails we fall back to placeholders that still let the
 * reporter attribute the leak to a process.
 */
function readActiveTask(): { name: string; file: string } {
  const worker = (
    globalThis as unknown as { __vitest_worker__?: { current?: VitestTaskLike } }
  ).__vitest_worker__
  const current = worker?.current
  const name = current?.name ?? '<unknown test>'
  const file = current?.file?.filepath ?? current?.file?.name ?? '<unknown file>'
  return { name, file }
}

beforeEach(() => {
  snapshotIds = new Set(liveHandles.keys())
})

afterEach(async () => {
  if (snapshotIds === null) return
  const snapshot = snapshotIds
  snapshotIds = null

  // Drain pending ``destroy`` async-hook callbacks before we diff.
  // Two failure modes need accommodating:
  //   1. Handles cleared during user ``afterEach`` (``clearTimeout`` in
  //      ``dismissAll()``, etc.) -- Node runs their ``destroy`` callback
  //      in a later event-loop pass, not synchronously.
  //   2. Handles scheduled in chains where each fired callback schedules
  //      the next (recharts/d3-timer rAF loops, polyfilled here as
  //      ``setTimeout(0)`` in ``test-setup.tsx``).
  // We yield via ``setImmediate`` until the live-handle set stops
  // shrinking, capped at ``MAX_DRAIN_ITERATIONS`` to bound worst-case
  // teardown cost. A genuine forgotten timer (no clear, no firing
  // callback) never shrinks the set and is reported.
  let lastSize = -1
  for (let i = 0; i < MAX_DRAIN_ITERATIONS; i++) {
    const before = liveHandles.size
    if (before === lastSize) break
    lastSize = before
    await setImmediateP()
  }

  const task = readActiveTask()
  const now = performance.now()
  const leaks: LeakRecord[] = []

  for (const [asyncId, info] of liveHandles) {
    if (snapshot.has(asyncId)) continue
    // Materialise the deferred stack only for leaked handles. For a
    // healthy test this loop body never runs, so the formatting cost
    // is paid once per genuine leak rather than once per tracked
    // resource init.
    const stack = info.error.stack ?? ''
    const userFrame = findUserFrame(stack)
    if (userFrame === null) continue
    const allow = matchAllowlist(info.type, stack)
    const attribution: LeakAttribution = allow !== null
      ? { allowlisted: true, allowlistReason: allow.reason }
      : { allowlisted: false, allowlistReason: null }
    leaks.push({
      ...attribution,
      asyncId,
      type: info.type,
      testName: task.name,
      testFile: task.file,
      userFrame,
      stack,
      ageMs: now - info.createdAtMs,
      mode,
    })
  }

  if (leaks.length === 0) return

  for (const leak of leaks) {
    recordLeak(leak)
  }

  const unallowed = leaks.filter(leak => !leak.allowlisted)
  if (unallowed.length === 0) return

  if (mode === 'fail') {
    const summary = unallowed
      .map(
        leak =>
          `  - ${leak.type} (asyncId=${String(leak.asyncId)}) created at ${leak.userFrame ?? '<no user frame>'}`,
      )
      .join('\n')
    throw new Error(
      `[active-handle-tracker] test "${task.name}" leaked ${String(unallowed.length)} active handle(s):\n${summary}\n` +
        `See web/test-infra/active-handle-tracker.ts for detail.`,
    )
  }
})
