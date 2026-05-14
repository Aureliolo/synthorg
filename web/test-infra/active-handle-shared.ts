/**
 * Types and pure helpers shared between the active-handle tracker
 * (worker-side, vitest setupFile) and the active-handle reporter
 * (main-process, vitest reporter). This module MUST NOT import from
 * ``vitest`` (or any sibling that does) so it can be loaded from
 * ``vitest.config.ts`` without vitest crashing on "vitest imported
 * inside config file".
 */

import { join, resolve, sep } from 'node:path'

/**
 * Resource types we treat as event-loop-holding. The set is curated:
 * we intentionally exclude `PROMISE`, `Microtask`, `GETADDRINFOREQWRAP`,
 * `GETNAMEINFOREQWRAP`, `DNSCHANNEL` and similar request-style
 * resources that settle on their own. Keeping the filter tight is
 * what makes the signal precise, and keeps init-time stack capture
 * cheap.
 *
 * If a new bug class turns up that uses a handle type missing here
 * (e.g. `MESSAGEPORT` from `worker_threads`), add it.
 */
export const TRACKED_TYPES_LIST = [
  'Timeout',
  'Immediate',
  'TCPWRAP',
  'TCPSERVERWRAP',
  'TCPCONNECTWRAP',
  'UDPWRAP',
  'UDPSENDWRAP',
  'PIPEWRAP',
  'PIPECONNECTWRAP',
  'TLSWRAP',
  'FSEVENTWRAP',
  'FSREQCALLBACK',
  'HTTPCLIENTREQUEST',
  'HTTPINCOMINGMESSAGE',
  'HTTP2SESSION',
  'HTTP2STREAM',
  'HTTP2PING',
  'HTTP2SETTINGS',
  'ZLIB',
  'CHILDPROCESS',
  'SIGNALWRAP',
  'STATWATCHER',
  'WRITEWRAP',
  'SHUTDOWNWRAP',
  'MESSAGEPORT',
] as const

/** Literal union of every tracked resource type. Single source of
 * truth for the tracker, the reporter, and the allowlist; stringly
 * typing instead admits records with invalid `type` values that
 * callers cannot autocomplete or narrow on. */
export type LeakType = (typeof TRACKED_TYPES_LIST)[number]

export type LeakMode = 'log' | 'fail'

/** Per-leak attribution. Encoded as a tagged union so the invariant
 * `allowlisted` iff `allowlistReason !== null` is enforced by the type
 * system, not by convention. */
export type LeakAttribution =
  | { allowlisted: true; allowlistReason: string }
  | { allowlisted: false; allowlistReason: null }

export type LeakRecord = LeakAttribution & {
  asyncId: number
  type: LeakType
  testName: string
  testFile: string
  userFrame: string | null
  stack: string
  ageMs: number
  mode: LeakMode
}

/** Subdirectory of the project root used for per-worker NDJSON logs
 * and the aggregated telemetry artifact. The tracker and reporter
 * both resolve their default path against this constant, so the two
 * halves of the gate cannot drift. */
export const DEFAULT_LEAK_LOG_SUBDIR = '.test-tmp'

/**
 * Resolve a candidate leak-log directory against the project root and
 * reject paths that escape it. Without this guard, an unsanitised
 * ``ACTIVE_HANDLE_LOG_DIR`` (set in CI, a container image, or a
 * shared developer machine) would let the reporter's ``rmSync`` wipe
 * an arbitrary directory at test-run start and let the tracker write
 * NDJSON to an arbitrary path.
 */
export function resolveLeakLogDir(
  raw: string | undefined,
  projectRoot: string = process.cwd(),
): string {
  const root = resolve(projectRoot)
  if (raw === undefined || raw === '') {
    return join(root, DEFAULT_LEAK_LOG_SUBDIR)
  }
  const resolved = resolve(root, raw)
  if (resolved !== root && !resolved.startsWith(root + sep)) {
    throw new Error(
      `ACTIVE_HANDLE_LOG_DIR must resolve inside ${root}; got: ${raw}`,
    )
  }
  return resolved
}
