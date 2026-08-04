/**
 * Ordering for provider-health writes that race each other.
 *
 * A recheck asks the provider now and produces a verdict nothing else
 * has; a read replays whatever verdict the server last stored. So the two
 * are not interchangeable, and a read that resolves late must not be
 * allowed to undo a recheck that landed while it was in flight. Both the
 * detail read and the list's per-provider health fan-out could do exactly
 * that, each guarding only against newer reads of its own kind.
 *
 * Only a recheck bumps this, immediately before it applies. A read
 * captures the number before it goes out and drops its own health if it
 * has moved on, keeping the rest of its payload, because only health is
 * contested.
 */

let revision = 0

/** Claim the newest health write. Call immediately before applying it. */
export function bumpHealthRevision(): number {
  revision += 1
  return revision
}

/**
 * The revision a read should capture before it starts.
 *
 * @returns The current health revision.
 */
export function currentHealthRevision(): number {
  return revision
}

/** Reset between tests, which share the module across cases. */
export function resetHealthRevision(): void {
  revision = 0
}
