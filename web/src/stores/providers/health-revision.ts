/**
 * Ordering for provider-health writes that race each other.
 *
 * Two health writers reach `selectedProviderHealth`: the all-provider
 * sweep, and the trailing detail read an individual recheck fires to
 * refresh the rest of the page. The detail read guards itself against
 * newer *detail* reads, but nothing told it about a newer *health* write,
 * so a slow detail read could resolve after a sweep and put the verdict
 * the sweep had just replaced back on screen.
 *
 * A health writer bumps this before it applies; a trailing read captures
 * it first and drops its own health if the number has moved on. The rest
 * of that read's payload still applies, because only health is contested.
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
