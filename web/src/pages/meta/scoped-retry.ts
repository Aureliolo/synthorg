/**
 * Shared retry-target resolution for the meta chat surfaces (Act / Group).
 *
 * "Try again" on an error bubble must replay the human instruction that
 * preceded THAT bubble, not the transcript tail: with multiple failed turns an
 * unscoped retry would resend the most recent human message instead of the one
 * the operator clicked on.
 */

interface RetryableMessage {
  id: number
  content: string
}

/**
 * Return the content of the last human-authored message before ``beforeMsgId``
 * (exclusive), or the most recent one when ``beforeMsgId`` is undefined.
 * Returns ``null`` when no human message is in scope. ``isHuman`` selects the
 * human-turn discriminator, which differs per surface (``kind === 'human'`` for
 * Act/Group, ``role === 'user'`` for Chat).
 */
export function resolveScopedRetryContent<M extends RetryableMessage>(
  messages: readonly M[],
  beforeMsgId: number | undefined,
  isHuman: (message: M) => boolean,
): string | null {
  const cutoff =
    beforeMsgId === undefined
      ? messages.length
      : messages.findIndex((m) => m.id === beforeMsgId)
  const scoped = messages.slice(0, cutoff < 0 ? messages.length : cutoff)
  return [...scoped].reverse().find(isHuman)?.content ?? null
}
