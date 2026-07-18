import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { PlanItemComment } from '@/api/types/plans'
import { PlanItemComments } from '@/pages/plans/PlanItemComments'

function comment(overrides?: Partial<PlanItemComment>): PlanItemComment {
  return {
    id: 'c1',
    plan_id: 'p1',
    item_id: 'i1',
    author: 'reviewer',
    author_kind: 'human',
    author_agent_id: null,
    reply_to_id: null,
    body: 'Consider a smaller first slice.',
    created_at: '2026-07-02T10:00:00Z',
    ...overrides,
  }
}

describe('PlanItemComments', () => {
  it('renders the existing thread with a count', () => {
    render(<PlanItemComments comments={[comment()]} onSubmit={vi.fn()} />)
    expect(screen.getByText('Discussion (1)')).toBeInTheDocument()
    expect(screen.getByText('reviewer')).toBeInTheDocument()
    expect(screen.getByText('Consider a smaller first slice.')).toBeInTheDocument()
  })

  it('marks an agent reply distinctly from a human comment', () => {
    const human = comment({ id: 'h1', author: 'ceo', body: 'Why this ledger?' })
    const agentReply = comment({
      id: 'a1',
      author: 'Casey',
      author_kind: 'agent',
      author_agent_id: 'agent-cfo',
      reply_to_id: 'h1',
      body: 'It nets out FX exposure.',
    })
    render(<PlanItemComments comments={[human, agentReply]} onSubmit={vi.fn()} />)
    // The agent reply carries an "agent" attribution the human comment lacks.
    expect(screen.getByText('agent')).toBeInTheDocument()
    expect(screen.getByText('Casey')).toBeInTheDocument()
    expect(screen.getByText('It nets out FX exposure.')).toBeInTheDocument()
  })

  it('posts a trimmed comment and clears the box on success', async () => {
    const onSubmit = vi.fn().mockResolvedValue({ id: 'c2' })
    render(<PlanItemComments comments={[]} onSubmit={onSubmit} />)
    const input = screen.getByLabelText('Add a comment')
    await userEvent.type(input, '  needs a rollback plan  ')
    await userEvent.click(screen.getByRole('button', { name: /Comment/ }))
    expect(onSubmit).toHaveBeenCalledWith('needs a rollback plan')
    expect(input).toHaveValue('')
  })

  it('keeps the draft when the post fails', async () => {
    const onSubmit = vi.fn().mockResolvedValue(null)
    render(<PlanItemComments comments={[]} onSubmit={onSubmit} />)
    const input = screen.getByLabelText('Add a comment')
    await userEvent.type(input, 'unsaved thought')
    await userEvent.click(screen.getByRole('button', { name: /Comment/ }))
    expect(input).toHaveValue('unsaved thought')
  })
})
