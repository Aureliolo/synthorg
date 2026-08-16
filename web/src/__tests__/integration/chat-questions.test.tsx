/**
 * Integration test: a parked agent question surfaces in the unified chat and
 * is answerable there.
 *
 * The WS binding does nothing but hand the frame to the store, so the socket
 * is driven through ``handleWsEvent`` directly; what is under test is that the
 * page renders the card and that resolving it reaches the API.
 */
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router'
import { describe, expect, it } from 'vitest'

import type {
  answerParkedQuestion,
  declineParkedQuestion,
} from '@/api/endpoints/chat-questions'
import type { QuestionDecisionResult } from '@/api/types/chat-questions'
import type { WsEvent } from '@/api/types/websocket'
import {
  openQuestionsHandler,
  parkedQuestionFixture,
  successFor,
} from '@/mocks/handlers'
import ChatPage from '@/pages/ChatPage'
import { useOrgQuestionsStore } from '@/stores/org-questions'
import { server } from '@/test-setup'

const QUESTION = 'Which database backend should I target?'
const ANSWER_URL = '/api/v1/meta/chat/questions/:approvalId/answer'
const DECLINE_URL = '/api/v1/meta/chat/questions/:approvalId/decline'
// Every control on a question card is named after that question, so several
// open cards (and the page composer) never share an accessible name.
const ANSWER_LABEL = `Answer: Dana Dev asks "${QUESTION}"`
const SEND_LABEL = `Send answer: Dana Dev asks "${QUESTION}"`
const DECLINE_LABEL = `Decline: Dana Dev asks "${QUESTION}"`

const SUBMITTED: WsEvent = {
  event_type: 'approval.submitted',
  channel: 'approvals',
  timestamp: '2026-08-02T10:00:00Z',
  payload: { approval: { id: 'question-1', action_type: 'clarify:question' } },
}

function renderChat() {
  return render(
    <MemoryRouter>
      <ChatPage />
    </MemoryRouter>,
  )
}

function decisionResult(approvalId: string, answer: string): QuestionDecisionResult {
  return {
    approval_id: approvalId,
    status: 'approved',
    recorded_answer: answer,
    decided_at: '2026-08-02T10:05:00Z',
  }
}

describe('parked questions in the unified chat', () => {
  it('renders a question that arrives over the socket with no conversation open', async () => {
    renderChat()
    expect(screen.getByText('Talk to your organisation')).toBeInTheDocument()

    server.use(openQuestionsHandler([parkedQuestionFixture({ question: QUESTION })]))
    act(() => {
      useOrgQuestionsStore.getState().handleWsEvent(SUBMITTED)
    })

    expect(await screen.findByText(QUESTION)).toBeInTheDocument()
    // The org has spoken, so the "talk to your organisation" invitation is wrong.
    expect(screen.queryByText('Talk to your organisation')).not.toBeInTheDocument()
  })

  it('hydrates an unanswered question on mount, so a reload does not lose it', async () => {
    server.use(
      openQuestionsHandler([
        parkedQuestionFixture({
          question: QUESTION,
          asked_by_name: 'Dana Dev',
          reversibility: 'hard_to_reverse',
        }),
      ]),
    )
    renderChat()

    expect(await screen.findByText(QUESTION)).toBeInTheDocument()
    expect(screen.getByText(/Dana Dev is asking/)).toBeInTheDocument()
    expect(screen.getByText('Hard to reverse')).toBeInTheDocument()
  })

  it('says the asker is unknown when the server could not name them', async () => {
    // The asker is the subject of the sentence, so an unnamed one reads as
    // unknown. The server sends null rather than the id precisely so this
    // card can never say "<uuid> is asking".
    const askerId = 'd83b8bfd-156f-49c1-b596-850d09170be5'
    server.use(
      openQuestionsHandler([
        parkedQuestionFixture({
          question: QUESTION,
          asked_by_id: askerId,
          asked_by_name: null,
        }),
      ]),
    )
    renderChat()

    expect(await screen.findByText(QUESTION)).toBeInTheDocument()
    expect(screen.getByText(/Unknown agent is asking/)).toBeInTheDocument()
    expect(screen.queryByText(new RegExp(askerId))).not.toBeInTheDocument()
  })

  it('answers in place with an idempotency key and drops the card', async () => {
    const posted: { answer: unknown; key: string | null }[] = []
    server.use(
      openQuestionsHandler([parkedQuestionFixture({ question: QUESTION })]),
      http.post(ANSWER_URL, async ({ request, params }) => {
        const body = (await request.json()) as Record<string, unknown>
        posted.push({
          answer: body['answer'],
          key: request.headers.get('Idempotency-Key'),
        })
        return HttpResponse.json(
          successFor<typeof answerParkedQuestion>(
            decisionResult(String(params['approvalId']), 'Postgres.'),
          ),
        )
      }),
    )
    const user = userEvent.setup()
    renderChat()
    await screen.findByText(QUESTION)

    await user.type(screen.getByLabelText(ANSWER_LABEL), 'Postgres.')
    await user.click(screen.getByRole('button', { name: SEND_LABEL }))

    await waitFor(() => expect(posted).toHaveLength(1))
    expect(posted[0]?.answer).toBe('Postgres.')
    expect(posted[0]?.key).toBeTruthy()
    await waitFor(() => expect(screen.queryByText(QUESTION)).not.toBeInTheDocument())
  })

  it('declines in place with an idempotency key and drops the card', async () => {
    const keys: (string | null)[] = []
    server.use(
      openQuestionsHandler([parkedQuestionFixture({ question: QUESTION })]),
      http.post(DECLINE_URL, ({ request, params }) => {
        keys.push(request.headers.get('Idempotency-Key'))
        return HttpResponse.json(
          successFor<typeof declineParkedQuestion>(
            decisionResult(String(params['approvalId']), 'declined'),
          ),
        )
      }),
    )
    const user = userEvent.setup()
    renderChat()
    await screen.findByText(QUESTION)

    await user.click(screen.getByRole('button', { name: DECLINE_LABEL }))

    await waitFor(() => expect(keys).toHaveLength(1))
    expect(keys[0]).toBeTruthy()
    await waitFor(() => expect(screen.queryByText(QUESTION)).not.toBeInTheDocument())
  })

  it('renders a question after the turns already in the conversation', async () => {
    server.use(openQuestionsHandler([parkedQuestionFixture({ question: QUESTION })]))
    const user = userEvent.setup()
    renderChat()
    await screen.findByText(QUESTION)

    await user.type(
      screen.getByLabelText('Message the organisation'),
      'how are we doing?',
    )
    await user.click(screen.getByRole('button', { name: 'Send message' }))

    await screen.findByText('The organisation is healthy.')
    const transcript = document.body.textContent
    expect(transcript.indexOf('how are we doing?')).toBeLessThan(
      transcript.indexOf(QUESTION),
    )
  })
})
