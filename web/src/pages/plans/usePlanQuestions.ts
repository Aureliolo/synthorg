import { useCallback, useEffect, useRef, useState } from 'react'

import { paginateAll } from '@/api/client'
import { listApprovals } from '@/api/endpoints/approvals'
import type { ApprovalResponse } from '@/api/types/approvals'
import { createLogger } from '@/lib/logger'
import { useApprovalsStore } from '@/stores/approvals'
import { usePlansStore } from '@/stores/plans'
import { sanitizeForLog } from '@/utils/logging'

const log = createLogger('plan-questions')

// One page of PENDING PLAN REVIEWS, the same bounded set the plan-approval
// lookup walks, scoped by ``source`` so a plan's questions are found however
// many unrelated approvals are outstanding.
const PENDING_REVIEW_FETCH_LIMIT = 200

// Mirrors CLARIFY_ACTION_TYPE (approval/questions.py). The plan's own
// ``plan:approve`` review is parked under the same source and the same
// ``plan_id``, so the action type is half the key: matching on the plan alone
// would answer the plan's approval with the text of a question.
const CLARIFY_ACTION = 'clarify:question'

export interface PlanQuestionsController {
  /** The approval awaiting an answer for *question*, or ``undefined``. */
  approvalFor: (question: string) => string | undefined
  /** True while the parked questions are being resolved. */
  resolving: boolean
  /** True when the lookup failed, so the panel can offer a retry. */
  lookupFailed: boolean
  /** The approval currently being answered, so its row can show progress. */
  submittingId: string | undefined
  /** Send the operator's answer; the backend writes it onto the plan. */
  answer: (approvalId: string, text: string) => Promise<void>
  retry: () => Promise<void>
}

/**
 * Resolve every question this plan has parked for a person, keyed by the
 * question text the panel renders.
 *
 * Two approvals can carry the same text (a planner may surface one question
 * per item it blocks), and one answer settles one of them, so the ids are
 * queued per question rather than replacing each other.
 */
function parkedByQuestion(
  reviews: readonly ApprovalResponse[],
  planId: string,
): Map<string, string[]> {
  const byQuestion = new Map<string, string[]>()
  for (const approval of reviews) {
    if (approval.metadata['plan_id'] !== planId) continue
    if (approval.action_type !== CLARIFY_ACTION) continue
    const queued = byQuestion.get(approval.description)
    if (queued === undefined) byQuestion.set(approval.description, [approval.id])
    else queued.push(approval.id)
  }
  return byQuestion
}

async function findParkedQuestions(planId: string): Promise<Map<string, string[]>> {
  const reviews = await paginateAll<ApprovalResponse>((cursor) =>
    listApprovals({
      source: 'plan_review',
      status: 'pending',
      limit: PENDING_REVIEW_FETCH_LIMIT,
      cursor,
    }),
  )
  return parkedByQuestion(reviews, planId)
}

/**
 * The answer path for a plan's open questions.
 *
 * The planner parks each unresolved question as a real ``clarify:question``
 * approval, and approving one with the operator's text is what writes that
 * answer back onto the plan the agents execute. This resolves those approvals
 * so the question can be answered where it is read: the generic Approvals
 * inbox excludes every ``plan_review`` row by design, so the plan's own page
 * is the only surface that can decide them.
 */
export function usePlanQuestions(planId: string): PlanQuestionsController {
  const [parked, setParked] = useState<Map<string, string[]>>(() => new Map())
  const [resolving, setResolving] = useState(true)
  const [lookupFailed, setLookupFailed] = useState(false)
  const [submittingId, setSubmittingId] = useState<string | undefined>(undefined)
  // Monotonic plan generation, bumped on every (re)lookup: a late response
  // from the previous plan must never populate this one's controls.
  const generationRef = useRef(0)

  const resolveQuestions = useCallback(async () => {
    const generation = (generationRef.current += 1)
    setParked(new Map())
    setLookupFailed(false)
    setSubmittingId(undefined)
    setResolving(true)
    try {
      const found = await findParkedQuestions(planId)
      if (generation !== generationRef.current) return
      setParked(found)
    } catch (err) {
      if (generation !== generationRef.current) return
      log.error('Failed to resolve the plan questions', sanitizeForLog(err))
      // Surfaced rather than swallowed: silently missing controls read as
      // "this question cannot be answered", which is the defect this fixes.
      setLookupFailed(true)
    } finally {
      if (generation === generationRef.current) setResolving(false)
    }
  }, [planId])

  useEffect(() => {
    void resolveQuestions()
  }, [resolveQuestions])

  const approvalFor = useCallback(
    (question: string) => parked.get(question)?.[0],
    [parked],
  )

  const answer = useCallback(
    async (approvalId: string, text: string) => {
      const generation = generationRef.current
      setSubmittingId(approvalId)
      const result = await useApprovalsStore
        .getState()
        .approveOne(approvalId, { comment: text })
      if (generation !== generationRef.current) return
      setSubmittingId(undefined)
      if (!result) return
      // The answer lands on the plan, so the plan is what the panel re-reads;
      // the parked set is re-resolved because this question is now decided.
      await usePlansStore.getState().fetchPlanDetail(planId)
      await resolveQuestions()
    },
    [planId, resolveQuestions],
  )

  return {
    approvalFor,
    resolving,
    lookupFailed,
    submittingId,
    answer,
    retry: resolveQuestions,
  }
}
