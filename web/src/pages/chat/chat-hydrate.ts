import type { ConversationTurnRecord } from '@/api/endpoints/meta'

import type { GroupMessage, RequestWorkMessage } from './chat-types'
import { nextMessageId } from './message-id'

/**
 * Reconstruct transcript bubbles from persisted conversation turns.
 *
 * Resume is text-only by design: a turn row carries the rendered content
 * and attribution, but not the ephemeral parked-proposal summaries or the
 * responder role label, so a resumed assistant/agent turn shows its text
 * without the in-context approval links or the routed-role badge (the
 * parked items still live in the approvals queue). Which mode a
 * conversation resumes into is decided by its ``kind``: ``group`` into the
 * group surface, ``direct``/``routed`` into Request work.
 */

/** Map persisted turns to Request-work transcript bubbles. */
export function hydrateWorkMessages(
  turns: readonly ConversationTurnRecord[],
): RequestWorkMessage[] {
  return turns.map((turn) => ({
    id: nextMessageId(),
    role: turn.role === 'user' ? 'user' : 'assistant',
    content: turn.content,
    ...(turn.author_name != null && { responderName: turn.author_name }),
    ...(turn.routed_topic != null && { routedTopic: turn.routed_topic }),
  }))
}

/** Map persisted turns to group-chat transcript bubbles. */
export function hydrateGroupMessages(
  turns: readonly ConversationTurnRecord[],
): GroupMessage[] {
  return turns.map((turn) => {
    if (turn.role === 'user') {
      return { id: nextMessageId(), kind: 'human', content: turn.content }
    }
    return {
      id: nextMessageId(),
      kind: 'agent',
      content: turn.content,
      ...(turn.author_name != null && { agentName: turn.author_name }),
    }
  })
}
