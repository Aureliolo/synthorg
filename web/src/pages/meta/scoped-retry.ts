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
  kind: string
  content: string
}

/**
 * Return the content of the last ``human`` message before ``beforeMsgId``
 * (exclusive), or the most recent human message when ``beforeMsgId`` is
 * undefined. Returns ``null`` when no human message is in scope.
 */
export function resolveScopedRetryContent(
  messages: readonly RetryableMessage[],
  beforeMsgId: number | undefined,
): string | null {
  const cutoff =
    beforeMsgId === undefined
      ? messages.length
      : messages.findIndex((m) => m.id === beforeMsgId)
  const scoped = messages.slice(0, cutoff < 0 ? messages.length : cutoff)
  return [...scoped].reverse().find((m) => m.kind === 'human')?.content ?? null
}
