let sequence = 0

/**
 * Monotonic id for a rendered chat message.
 *
 * Module-level (not a component ref) so ids stay unique and stable across
 * a panel remount: the conversation transcript now lives in a store, but
 * the id generator must not reset when the mode panel unmounts and
 * remounts, or a resumed/switched-back transcript could collide keys.
 */
export function nextMessageId(): number {
  sequence += 1
  return sequence
}

/** Reset the id sequence. Test-only: keeps ids deterministic per test. */
export function resetMessageIds(): void {
  sequence = 0
}
