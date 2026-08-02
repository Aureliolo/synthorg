/**
 * Client for the parked-question surface on the unified conversation.
 *
 * The org asks a question when an agent stops mid-run; these read the open
 * ones and answer them, which resumes the agent.
 */

import type {
  AnswerQuestionRequest,
  ParkedQuestion,
  QuestionDecisionResult,
} from '@/api/types/chat-questions'

import { apiClient, unwrap, unwrapPaginated, type PaginatedResult } from '../client'
import { idempotencyKeyHeader } from '../idempotency'
import type {
  ApiResponse,
  PaginatedResponse,
  PaginationParams,
} from '../types/http'

const BASE = '/meta/chat/questions'

/**
 * How many open questions the chat page renders at once.
 *
 * One page, not the whole set. The server already orders by what blocks most
 * (hard-to-reverse first, then oldest), so the first page is the right first
 * page, and walking every cursor would make the chat page's cost scale with
 * the size of the backlog it exists to help drain.
 */
const QUESTION_PAGE_SIZE = 50

/**
 * The open questions the org is waiting on, hard-to-reverse first.
 *
 * Returns the first page plus whether more exist, so the transcript can say
 * so rather than silently rendering a truncated list.
 */
export async function listParkedQuestions(): Promise<
  PaginatedResult<ParkedQuestion>
> {
  const params: PaginationParams = { limit: QUESTION_PAGE_SIZE }
  const response = await apiClient.get<PaginatedResponse<ParkedQuestion>>(BASE, {
    params,
  })
  return unwrapPaginated<ParkedQuestion>(response)
}

/** Answer a parked question, resuming the agent with the answer. */
export async function answerParkedQuestion(
  approvalId: string,
  data: AnswerQuestionRequest,
  idempotencyKey?: string,
): Promise<QuestionDecisionResult> {
  const response = await apiClient.post<ApiResponse<QuestionDecisionResult>>(
    `${BASE}/${encodeURIComponent(approvalId)}/answer`,
    data,
    { headers: idempotencyKeyHeader(idempotencyKey) },
  )
  return unwrap(response)
}

/** Decline to answer; the agent resumes on its own judgement. */
export async function declineParkedQuestion(
  approvalId: string,
  idempotencyKey?: string,
): Promise<QuestionDecisionResult> {
  const response = await apiClient.post<ApiResponse<QuestionDecisionResult>>(
    `${BASE}/${encodeURIComponent(approvalId)}/decline`,
    undefined,
    { headers: idempotencyKeyHeader(idempotencyKey) },
  )
  return unwrap(response)
}
