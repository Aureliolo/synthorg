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

import {
  apiClient,
  paginateAll,
  unwrap,
  unwrapPaginated,
  type PaginatedResult,
} from '../client'
import { idempotencyKeyHeader } from '../idempotency'
import type {
  ApiResponse,
  PaginatedResponse,
  PaginationParams,
} from '../types/http'

const BASE = '/meta/chat/questions'

function pageParams(cursor: string | null): PaginationParams {
  return cursor ? { cursor } : {}
}

async function fetchQuestionsPage(
  cursor: string | null,
): Promise<PaginatedResult<ParkedQuestion>> {
  const response = await apiClient.get<PaginatedResponse<ParkedQuestion>>(BASE, {
    params: pageParams(cursor),
  })
  return unwrapPaginated<ParkedQuestion>(response)
}

/**
 * Every question the org is currently waiting on, hard-to-reverse first.
 *
 * The whole set is loaded (the server orders it) so the chat page can render
 * the cards without a second source of truth for the ordering.
 */
export async function listParkedQuestions(): Promise<ParkedQuestion[]> {
  return paginateAll<ParkedQuestion>(fetchQuestionsPage)
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
