import type { OrgQuestionRecord } from '@/stores/org-questions'
import { UNKNOWN_AGENT_NAME } from '@/utils/agents'

import type { OrgTurn } from './org-chat-types'

/**
 * Project the open questions onto transcript turns.
 *
 * Pure, so the ordering the server chose (hard-to-reverse first, then oldest
 * first) is preserved verbatim rather than re-derived in the browser.
 */
export function toQuestionTurns(records: readonly OrgQuestionRecord[]): OrgTurn[] {
  return records.map(({ question, turnId }) => ({
    id: turnId,
    kind: 'event' as const,
    event: {
      type: 'question' as const,
      approvalId: question.approval_id,
      question: question.question,
      // The asker is the subject of the sentence the card renders, so an
      // unresolved one says so plainly; the server sends null rather than the
      // identifier precisely so the transcript never reads "<uuid> is asking".
      askedByName: question.asked_by_name ?? UNKNOWN_AGENT_NAME,
      taskTitle: question.task_title ?? undefined,
      project: question.project ?? undefined,
      hardToReverse: question.reversibility === 'hard_to_reverse',
      isDecision: question.is_decision,
      options: question.options.map((option) => ({
        id: option.id,
        title: option.title,
        summary: option.summary,
        recommended: option.recommended,
      })),
      askedAt: question.asked_at,
    },
  }))
}
