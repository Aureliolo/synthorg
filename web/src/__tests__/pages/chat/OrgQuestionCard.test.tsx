import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { describe, expect, it, vi } from 'vitest'

import type { QuestionEvent } from '@/pages/chat/org-chat-types'
import { OrgQuestionCard } from '@/pages/chat/OrgQuestionCard'

function questionEvent(overrides: Partial<QuestionEvent> = {}): QuestionEvent {
  return {
    type: 'question',
    approvalId: 'question-1',
    question: 'Which database backend should I target?',
    askedByName: 'Dana Dev',
    hardToReverse: false,
    options: [],
    askedAt: '2026-08-02T10:00:00Z',
    ...overrides,
  }
}

function renderCard(
  event: QuestionEvent,
  handlers: {
    resolving?: boolean
    onAnswer?: (approvalId: string, answer: string, chosenOptionId?: string) => void
    onDecline?: (approvalId: string) => void
  } = {},
) {
  const onAnswer = handlers.onAnswer ?? vi.fn()
  const onDecline = handlers.onDecline ?? vi.fn()
  render(
    <MemoryRouter>
      <OrgQuestionCard
        event={event}
        resolving={handlers.resolving ?? false}
        onAnswer={onAnswer}
        onDecline={onDecline}
      />
    </MemoryRouter>,
  )
  return { onAnswer, onDecline }
}

describe('OrgQuestionCard', () => {
  it('shows who is asking and what they asked', () => {
    renderCard(questionEvent({ taskTitle: 'Pick a store', project: 'checkout' }))
    expect(screen.getByText(/Dana Dev is asking/)).toBeInTheDocument()
    expect(
      screen.getByText('Which database backend should I target?'),
    ).toBeInTheDocument()
    expect(screen.getByText('Pick a store - checkout')).toBeInTheDocument()
  })

  it('marks a hard-to-reverse question and leaves a reversible one unmarked', () => {
    const { unmount } = render(
      <MemoryRouter>
        <OrgQuestionCard
          event={questionEvent({ hardToReverse: true })}
          resolving={false}
          onAnswer={vi.fn()}
          onDecline={vi.fn()}
        />
      </MemoryRouter>,
    )
    expect(screen.getByText('Hard to reverse')).toBeInTheDocument()
    unmount()

    renderCard(questionEvent())
    expect(screen.queryByText('Hard to reverse')).not.toBeInTheDocument()
  })

  it('keeps the clarification send disabled until the answer has content', async () => {
    const user = userEvent.setup()
    const { onAnswer } = renderCard(questionEvent())

    const send = screen.getByRole('button', { name: 'Send answer' })
    expect(send).toBeDisabled()

    await user.type(screen.getByLabelText('Answer the question'), '   ')
    expect(send).toBeDisabled()

    await user.type(screen.getByLabelText('Answer the question'), 'Postgres')
    expect(send).toBeEnabled()
    await user.click(send)
    expect(onAnswer).toHaveBeenCalledWith('question-1', 'Postgres')
  })

  it('renders one button per option and no free-text field for a decision', async () => {
    const user = userEvent.setup()
    const { onAnswer } = renderCard(
      questionEvent({
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

    expect(screen.queryByLabelText('Answer the question')).not.toBeInTheDocument()
    expect(screen.getByText('Recommended')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Choose SQLite' }))
    expect(onAnswer).toHaveBeenCalledWith('question-1', 'SQLite', 'opt-b')
  })

  it('declines with the approval id and says what declining does', async () => {
    const user = userEvent.setup()
    const { onDecline } = renderCard(questionEvent())

    expect(
      screen.getByText(/proceeds on its own judgement/i),
    ).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Decline' }))
    expect(onDecline).toHaveBeenCalledWith('question-1')
  })

  it('disables both paths while the answer is in flight', () => {
    renderCard(
      questionEvent({
        options: [
          { id: 'opt-a', title: 'Postgres', summary: 'Managed', recommended: false },
        ],
      }),
      { resolving: true },
    )
    expect(screen.getByRole('button', { name: 'Choose Postgres' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Decline' })).toBeDisabled()
  })
})
