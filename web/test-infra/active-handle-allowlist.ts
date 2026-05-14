/**
 * Explicit allowlist for active-handle leak detection.
 *
 * The active-handle tracker (./active-handle-tracker.ts) snapshots the
 * set of live `async_hooks` resources before each test and diffs it
 * after. Every surviving resource whose creation stack reaches a
 * `web/src/` frame is a candidate leak. An entry here suppresses one
 * such candidate when the leak is genuinely structural (i.e. owned by
 * a third-party runtime we cannot teardown without losing test
 * coverage), not when it is "annoying to fix".
 *
 * Adding an entry is a deliberate audit step:
 *   1. Confirm the resource is created by code we don't control AND
 *      cannot be released by the call site (e.g. a jsdom internal that
 *      jsdom itself never closes).
 *   2. Write the `reason` explaining why it can't be killed at source
 *      and what the structural floor is. "Tests pass with this entry
 *      removed" is sufficient evidence the entry is unnecessary.
 *   3. Cite a follow-up issue if upstream could fix it.
 *
 * If an entry has no clear structural justification, the leak is real
 * and the fix belongs in the test or the production code path, not
 * here.
 */

import type { LeakType } from './active-handle-shared'

export interface AllowlistEntry {
  /**
   * Resource type as reported by Node's `async_hooks` init callback.
   * Restricted to the tracker's `LeakType` literal union so entries
   * cannot reference resource names the tracker would never emit.
   */
  type: LeakType

  /**
   * Regex applied to the captured creation stack. The match is against
   * the full stack string (each frame is a line starting with `at `).
   * Use a tight pattern that names a specific frame so the entry can't
   * silently swallow unrelated leaks of the same type.
   */
  framePattern: RegExp

  /**
   * Human-readable reason for the entry. Required. The reporter prints
   * this in summary output so reviewers see why each suppression
   * exists at a glance.
   */
  reason: string
}

export const ALLOWLIST: AllowlistEntry[] = []
