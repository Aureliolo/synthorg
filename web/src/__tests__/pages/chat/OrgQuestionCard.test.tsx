import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { QuestionEvent } from '@/pages/chat/org-chat-types'
import { OrgQuestionCard } from '@/pages/chat/OrgQuestionCard'
import { useOrgQuestionsStore } from '@/stores/org-questions'

const QUESTION = 'Which database backend should I target?'
const ASKER = 'Dana Dev'
const ANSWER_LABEL = `Answer: ${ASKER} asks "${QUESTION}"`
const SEND_LABEL = `Send answer: ${ASKER} asks "${QUESTION}"`
const DECLINE_LABEL = `Decline: ${ASKER} asks "${QUESTION}"`

function questionEvent(overrides: Partial<QuestionEvent> = {}): QuestionEvent {
  return {
    type: 'question',
    approvalId: 'question-1',
    question: QUESTION,
    askedByName: ASKER,
    hardToReverse: false,
    isDecision: false,
    options: [],
    askedAt: '2026-08-02T10:00:00Z',
    ...overrides,
  }
}

/** Stand in for the two store actions the card calls directly. */
function stubStore(options: { resolving?: boolean } = {}) {
  const answerQuestion = vi.fn(() => Promise.resolve(true))
  const declineQuestion = vi.fn(() => Promise.resolve(true))
  useOrgQuestionsStore.setState({
    answerQuestion,
    declineQuestion,
    resolving: new Set(options.resolving === true ? ['question-1'] : []),
  })
  return { answerQuestion, declineQuestion }
}

function renderCard(event: QuestionEvent) {
  render(
    <MemoryRouter>
      <OrgQuestionCard event={event} />
    </MemoryRouter>,
  )
}

describe('OrgQuestionCard', () => {
  beforeEach(() => {
    useOrgQuestionsStore.getState().reset()
  })

  it('shows who is asking and what they asked', () => {
    stubStore()
    renderCard(questionEvent({ taskTitle: 'Pick a store', project: 'checkout' }))
    expect(screen.getByText(/Dana Dev is asking/)).toBeInTheDocument()
    expect(screen.getByText(QUESTION)).toBeInTheDocument()
    expect(screen.getByText('Pick a store - checkout')).toBeInTheDocument()
  })

  it('marks a hard-to-reverse question and leaves a reversible one unmarked', () => {
    stubStore()
    const { unmount } = render(
      <MemoryRouter>
        <OrgQuestionCard event={questionEvent({ hardToReverse: true })} />
      </MemoryRouter>,
    )
    expect(screen.getByText('Hard to reverse')).toBeInTheDocument()
    unmount()

    renderCard(questionEvent())
    expect(screen.queryByText('Hard to reverse')).not.toBeInTheDocument()
  })

  it('keeps the clarification send disabled until the answer has content', async () => {
    const user = userEvent.setup()
    const { answerQuestion } = stubStore()
    renderCard(questionEvent())

    const send = screen.getByRole('button', { name: SEND_LABEL })
    expect(send).toBeDisabled()

    await user.type(screen.getByLabelText(ANSWER_LABEL), '   ')
    expect(send).toBeDisabled()

    await user.type(screen.getByLabelText(ANSWER_LABEL), 'Postgres')
    expect(send).toBeEnabled()
    await user.click(send)
    expect(answerQuestion).toHaveBeenCalledWith('question-1', 'Postgres', undefined)
  })

  it('caps the answer at the length the endpoint accepts', () => {
    stubStore()
    renderCard(questionEvent())
    expect(screen.getByLabelText(ANSWER_LABEL)).toHaveAttribute('maxLength', '4096')
  })

  it('names its controls after its own question', () => {
    // Several questions can be open at once and the page composer renders its
    // own send button, so a bare "Send answer" would be ambiguous.
    stubStore()
    renderCard(questionEvent())
    expect(screen.getByRole('button', { name: SEND_LABEL })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: DECLINE_LABEL })).toBeInTheDocument()
    expect(
      screen.getByRole('link', {
        name: `Review in Approvals: ${ASKER} asks "${QUESTION}"`,
      }),
    ).toBeInTheDocument()
  })

  it('renders one button per option and no free-text field for a decision', async () => {
    const user = userEvent.setup()
    const { answerQuestion } = stubStore()
    renderCard(
      questionEvent({
        isDecision: true,
        options: [
          {
            id: 'opt-a',
            title: 'Postgres',
            summary: 'Managed, familiar',
            recommended: true,
          },
          { id: 'opt-b', title: 'SQLite', summary: 'Zero ops', recommended: false },
        ],
      }),
    )

    expect(screen.queryByLabelText(ANSWER_LABEL)).not.toBeInTheDocument()
    expect(screen.getByText('Recommended')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Choose SQLite' }))
    expect(answerQuestion).toHaveBeenCalledWith('question-1', 'SQLite', 'opt-b')
  })

  it('follows the server on which shape to render, not the option count', () => {
    // A decision whose options failed to project must not render a free-text
    // box the server would reject: the pick is the only answer it accepts.
    stubStore()
    renderCard(questionEvent({ isDecision: true, options: [] }))
    expect(screen.queryByLabelText(ANSWER_LABEL)).not.toBeInTheDocument()
  })

  it('declines through the store and says what declining does', async () => {
    const user = userEvent.setup()
    const { declineQuestion } = stubStore()
    renderCard(questionEvent())

    expect(screen.getByText(/proceeds on its own judgement/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: DECLINE_LABEL }))
    expect(declineQuestion).toHaveBeenCalledWith('question-1')
  })

  it('disables both paths while the answer is in flight', () => {
    stubStore({ resolving: true })
    renderCard(
      questionEvent({
        isDecision: true,
        options: [
          { id: 'opt-a', title: 'Postgres', summary: 'Managed', recommended: false },
        ],
      }),
    )
    expect(screen.getByRole('button', { name: 'Choose Postgres' })).toBeDisabled()
    expect(screen.getByRole('button', { name: DECLINE_LABEL })).toBeDisabled()
  })
})
