import type {
  ConversationKind,
  ConversationTurnRecord,
} from '@/api/endpoints/meta'
import type { TurnIntent } from '@/api/types'

import { nextMessageId } from './message-id'
import type { OrgTurn } from './org-chat-types'

/**
 * Reconstruct the unified transcript from persisted conversation turns.
 *
 * Resume is text-only by design: a turn row carries the rendered content plus
 * attribution, but not the ephemeral parked-proposal summaries or the routed
 * confidence, so a resumed agent turn shows its text and attribution without
 * the in-context approval links (the parked items still live in Approvals).
 */

/**
 * The pinned capability a resumed conversation continues as: a group thread
 * keeps convening, a direct/routed request-work thread keeps proposing, so a
 * follow-up turn stays in the same capability instead of being re-classified.
 */
export function activeIntentForKind(kind: ConversationKind): TurnIntent {
  return kind === 'group' ? 'group_convene' : 'propose'
}

/** Map persisted turns to unified-conversation transcript turns. */
export function hydrateOrgMessages(
  turns: readonly ConversationTurnRecord[],
): OrgTurn[] {
  return turns.map((turn): OrgTurn => {
    if (turn.role === 'user') {
      return {
        id: nextMessageId(),
        kind: 'human',
        content: turn.content,
        timestamp: turn.created_at,
      }
    }
    if (turn.author_name != null) {
      return {
        id: nextMessageId(),
        kind: 'agent',
        content: turn.content,
        agentName: turn.author_name,
        agentTopic: turn.routed_topic,
        timestamp: turn.created_at,
      }
    }
    return {
      id: nextMessageId(),
      kind: 'assistant',
      content: turn.content,
      timestamp: turn.created_at,
    }
  })
}
