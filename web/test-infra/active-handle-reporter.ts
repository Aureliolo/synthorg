/**
 * Main-process Vitest reporter for the active-handle tracker.
 *
 * The worker-side tracker (./active-handle-tracker.ts) appends one
 * NDJSON record per leaked handle to `web/.test-tmp/handle-leaks-<pid>.ndjson`.
 * This reporter wipes that directory at run start, reads it back at
 * run end, and emits a human-readable summary plus (optionally) a
 * structured telemetry artifact.
 *
 * In `log` mode (the default), the reporter NEVER fails the run; it
 * only surfaces what was observed. In `fail` mode, the worker-side
 * tracker throws inside `afterEach` so the failing test surfaces via
 * the standard vitest pipeline, not via this reporter, and this
 * reporter still prints the per-run summary so reviewers can see the
 * full picture.
 */

import { readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import type { Reporter } from 'vitest/node'

import {
  resolveLeakLogDir,
  type LeakMode,
  type LeakRecord,
} from './active-handle-shared'

export interface ActiveHandleReporterOptions {
  /**
   * Override directory for the per-worker NDJSON files. Defaults to
   * `<project-root>/.test-tmp`, matching the tracker's `LEAK_LOG_DIR`.
   * The path is resolved against the project root and must not escape
   * it; the same `resolveLeakLogDir` validator the tracker uses
   * applies here.
   */
  logDir?: string

  /**
   * When `true`, the reporter writes the aggregated summary to
   * `handle-telemetry.json` in `logDir` so CI can upload it as an
   * artifact. Defaults to `process.env.CI === 'true'`.
   */
  emitTelemetry?: boolean
}

export interface TelemetryArtifact {
  schemaVersion: 1
  generatedAt: string
  mode: LeakMode
  totalLeaks: number
  unallowedLeaks: number
  byType: Record<string, number>
  byTest: ReadonlyArray<{
    testName: string
    testFile: string
    leakCount: number
    types: string[]
  }>
  records: readonly LeakRecord[]
}

/**
 * Build a `TelemetryArtifact` from a flat list of `LeakRecord`s.
 * Centralises aggregate computation so that `totalLeaks`,
 * `unallowedLeaks`, `byType`, and `byTest` are derived from the same
 * input in one place, and cannot drift relative to `records`.
 */
export function createTelemetryArtifact(
  records: readonly LeakRecord[],
): TelemetryArtifact {
  // ``Object.create(null)`` produces a prototype-less object so an
  // ``r.type`` of ``__proto__`` (only reachable via a hand-crafted
  // NDJSON record in ``.test-tmp/`` since the typed write path is
  // constrained to ``LeakType``) cannot mutate ``Object.prototype``.
  const byType: Record<string, number> = Object.create(null) as Record<
    string,
    number
  >
  const buckets = new Map<string, LeakRecord[]>()
  for (const r of records) {
    byType[r.type] = (byType[r.type] ?? 0) + 1
    const key = `${r.testFile}::${r.testName}`
    let bucket = buckets.get(key)
    if (bucket === undefined) {
      bucket = []
      buckets.set(key, bucket)
    }
    bucket.push(r)
  }
  // Use the first record's ``testFile`` / ``testName`` directly rather
  // than decomposing the bucket key: vitest test descriptions may
  // legitimately contain ``::`` and a split would truncate them.
  const byTest = [...buckets.values()].flatMap(bucket => {
    // ``bucket[0]`` can be undefined for an empty bucket at runtime; the
    // test-infra tsconfig (tsconfig.node) lacks ``noUncheckedIndexedAccess`` so
    // the element type hides that, making this real guard look redundant.
    const first = bucket[0]
    // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition -- runtime empty-bucket guard the lax test-infra tsconfig types away
    if (first === undefined) return []
    return [{
      testName: first.testName,
      testFile: first.testFile,
      leakCount: bucket.length,
      types: [...new Set(bucket.map(r => r.type))],
    }]
  })
  return {
    schemaVersion: 1,
    generatedAt: new Date().toISOString(),
    mode: records[0]?.mode ?? 'log',
    totalLeaks: records.length,
    unallowedLeaks: records.filter(r => !r.allowlisted).length,
    byType,
    byTest,
    records,
  }
}

export default class ActiveHandleReporter implements Reporter {
  private readonly logDir: string
  private readonly emitTelemetry: boolean

  constructor(options: ActiveHandleReporterOptions = {}) {
    // Resolve through the same validator the tracker uses so a
    // hostile ``ACTIVE_HANDLE_LOG_DIR`` (or test-time options bag)
    // cannot redirect the ``rmSync`` below to a path outside the
    // project tree.
    this.logDir = resolveLeakLogDir(options.logDir)
    this.emitTelemetry =
      options.emitTelemetry ?? process.env.CI === 'true'
  }

  onTestRunStart(): void {
    // Fires at the start of every test run, including reruns in
    // ``vitest watch``. Wiping here (instead of in ``onInit``, which
    // only fires once per reporter lifecycle) ensures stale leak
    // records from a prior run cannot be aggregated into the current
    // run's summary.
    try {
      rmSync(this.logDir, { recursive: true, force: true })
    } catch {
      // First run: directory may not exist. Subsequent failures are
      // surfaced when the tracker fails to write to the same path.
    }
  }

  onTestRunEnd(): void {
    const records = this.readAllLeaks()
    this.printSummary(records)
    if (this.emitTelemetry) {
      this.writeTelemetry(records)
    }
  }

  private readAllLeaks(): LeakRecord[] {
    const records: LeakRecord[] = []
    let entries: string[]
    try {
      // eslint-disable-next-line security/detect-non-literal-fs-filename -- self-managed cache dir
      entries = readdirSync(this.logDir)
    } catch {
      return records
    }
    for (const entry of entries) {
      if (!entry.startsWith('handle-leaks-')) continue
      if (!entry.endsWith('.ndjson')) continue
      const path = join(this.logDir, entry)
      let body: string
      try {
        // eslint-disable-next-line security/detect-non-literal-fs-filename -- self-managed cache dir
        body = readFileSync(path, 'utf8')
      } catch {
        continue
      }
      for (const line of body.split('\n')) {
        const trimmed = line.trim()
        if (trimmed === '') continue
        try {
          records.push(JSON.parse(trimmed) as LeakRecord)
        } catch {
          // Malformed line: skip rather than abort the whole summary.
        }
      }
    }
    return records
  }

  private printSummary(records: LeakRecord[]): void {
    const out = process.stderr
    const total = records.length
    const unallowed = records.filter(r => !r.allowlisted)
    const mode = records[0]?.mode ?? 'log'

    if (total === 0) {
      out.write(
        '\n[active-handle-reporter] no leaked handles detected\n',
      )
      return
    }

    out.write(
      `\n[active-handle-reporter] mode=${mode} total=${String(total)} unallowed=${String(unallowed.length)}\n`,
    )

    const byType = new Map<string, number>()
    for (const r of records) {
      byType.set(r.type, (byType.get(r.type) ?? 0) + 1)
    }
    const typeRows = [...byType.entries()].sort((a, b) => b[1] - a[1])
    out.write('  by type:\n')
    for (const [type, count] of typeRows) {
      out.write(`    ${type.padEnd(22)} ${String(count)}\n`)
    }

    const byTestKey = new Map<string, LeakRecord[]>()
    for (const r of records) {
      const key = `${r.testFile}::${r.testName}`
      let bucket = byTestKey.get(key)
      if (bucket === undefined) {
        bucket = []
        byTestKey.set(key, bucket)
      }
      bucket.push(r)
    }
    const testRows = [...byTestKey.entries()].sort(
      (a, b) => b[1].length - a[1].length,
    )
    out.write('  top leaking tests:\n')
    for (const [key, bucket] of testRows.slice(0, 10)) {
      const types = [...new Set(bucket.map(r => r.type))].join(',')
      out.write(`    [${String(bucket.length)}] ${types} - ${key}\n`)
    }

    out.write('  representative stacks (one per (test, type) pair):\n')
    const seen = new Set<string>()
    for (const r of records) {
      const key = `${r.testFile}::${r.testName}::${r.type}`
      if (seen.has(key)) continue
      seen.add(key)
      out.write(
        `    - ${r.type} in "${r.testName}" (${r.testFile})\n`,
      )
      out.write(`      first user frame: ${r.userFrame ?? '<none>'}\n`)
      if (r.allowlisted) {
        out.write(`      allowlisted: ${r.allowlistReason}\n`)
      }
    }
  }

  private writeTelemetry(records: readonly LeakRecord[]): void {
    const artifact = createTelemetryArtifact(records)
    const path = join(this.logDir, 'handle-telemetry.json')
    try {
      // eslint-disable-next-line security/detect-non-literal-fs-filename -- self-managed cache dir
      writeFileSync(path, JSON.stringify(artifact, null, 2))
    } catch {
      process.stderr.write(
        `[active-handle-reporter] failed to write telemetry artifact at ${path}\n`,
      )
    }
  }
}
