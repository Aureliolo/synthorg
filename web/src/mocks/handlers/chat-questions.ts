import { http, HttpResponse } from 'msw'

import type {
  answerParkedQuestion,
  declineParkedQuestion,
  listParkedQuestions,
} from '@/api/endpoints/chat-questions'
import type { ParkedQuestion } from '@/api/types/chat-questions'

import { apiError, paginatedFor, successFor } from './helpers'

const BASE = '/api/v1/meta/chat/questions'
const PAGE_LIMIT = 50

/**
 * Build the one-page envelope the questions endpoint returns.
 *
 * Exported so a test that needs to wrap the resolver (to count requests, say)
 * can build the same body rather than reaching into the handler.
 */
export function questionsPage(questions: readonly ParkedQuestion[], hasMore = false) {
  const nextCursor = hasMore ? 'next' : null
  return paginatedFor<typeof listParkedQuestions>({
    data: [...questions],
    limit: PAGE_LIMIT,
    nextCursor,
    hasMore,
    pagination: { limit: PAGE_LIMIT, next_cursor: nextCursor, has_more: hasMore },
  })
}

/** Build one open question, overriding whatever the test cares about. */
export function parkedQuestionFixture(
  overrides: Partial<ParkedQuestion> = {},
): ParkedQuestion {
  return {
    approval_id: 'question-1',
    question: 'Which database backend should I target?',
    asked_by_id: 'agent-dev',
    asked_by_name: 'Dana Dev',
    task_id: null,
    task_title: null,
    project: null,
    reversibility: 'reversible',
    is_decision: false,
    options: [],
    asked_at: '2026-08-02T10:00:00Z',
    ...overrides,
  }
}

/** Override the list endpoint with a specific set of open questions. */
export function openQuestionsHandler(
  questions: readonly ParkedQuestion[],
  hasMore = false,
) {
  return http.get(BASE, () => HttpResponse.json(questionsPage(questions, hasMore)))
}

export const chatQuestionsHandlers = [
  http.get(BASE, () => HttpResponse.json(questionsPage([]))),
  http.post(`${BASE}/:approvalId/answer`, async ({ request, params }) => {
    const body: unknown = await request.json()
    const answer =
      body && typeof body === 'object'
        ? (body as Record<string, unknown>)['answer']
        : undefined
    if (typeof answer !== 'string' || !answer.trim()) {
      return HttpResponse.json(apiError('Answer must not be blank'), { status: 400 })
    }
    return HttpResponse.json(
      successFor<typeof answerParkedQuestion>({
        approval_id: String(params['approvalId']),
        status: 'approved',
        recorded_answer: answer,
        decided_at: '2026-08-02T10:05:00Z',
      }),
    )
  }),
  http.post(`${BASE}/:approvalId/decline`, ({ params }) =>
    HttpResponse.json(
      successFor<typeof declineParkedQuestion>({
        approval_id: String(params['approvalId']),
        status: 'rejected',
        recorded_answer: 'The operator declined to answer this question.',
        decided_at: '2026-08-02T10:05:00Z',
      }),
    ),
  ),
]
