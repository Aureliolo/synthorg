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
    // A top-level comment carries no reply parent.
    expect(onSubmit).toHaveBeenCalledWith('needs a rollback plan', undefined)
    expect(input).toHaveValue('')
  })

  it('renders a reply under its parent regardless of arrival order', () => {
    // The reply lands in the list AFTER an unrelated later comment, but must
    // still render grouped under the comment it answers.
    const parent = comment({ id: 'p', author: 'ceo', body: 'Why this ledger?' })
    const later = comment({
      id: 'later',
      author: 'cfo',
      body: 'Unrelated note.',
      created_at: '2026-07-02T10:05:00Z',
    })
    const reply = comment({
      id: 'r',
      author: 'Casey',
      author_kind: 'agent',
      author_agent_id: 'agent-cfo',
      reply_to_id: 'p',
      body: 'It nets out FX exposure.',
      created_at: '2026-07-02T10:10:00Z',
    })
    render(
      <PlanItemComments comments={[parent, later, reply]} onSubmit={vi.fn()} />,
    )
    // The parent's own row sits in its thread wrapper (row <li> -> <ul> -> thread
    // <li>); the reply groups into that same wrapper, while the unrelated later
    // comment forms its own thread and must not be pulled in.
    const parentRow = screen.getByText('Why this ledger?').closest('li')
    const threadItem = parentRow?.parentElement?.parentElement
    expect(threadItem?.tagName).toBe('LI')
    expect(threadItem).toHaveTextContent('It nets out FX exposure.')
    expect(threadItem).not.toHaveTextContent('Unrelated note.')
  })

  it('submits a reply with the parent id after choosing Reply', async () => {
    const onSubmit = vi.fn().mockResolvedValue({ id: 'r1' })
    const parent = comment({ id: 'p9', author: 'ceo', body: 'What is the risk?' })
    render(<PlanItemComments comments={[parent]} onSubmit={onSubmit} />)
    // Only the comment's Reply action exists before entering reply mode.
    await userEvent.click(screen.getByRole('button', { name: 'Reply' }))
    await userEvent.type(screen.getByLabelText('Write a reply'), 'Low, contained.')
    // In reply mode the composer's submit is also labelled "Reply"; it is the
    // last such button (the comment's own Reply action is first).
    const submit = screen.getAllByRole('button', { name: 'Reply' }).at(-1)
    if (submit === undefined) throw new Error('reply submit button missing')
    await userEvent.click(submit)
    expect(onSubmit).toHaveBeenCalledWith('Low, contained.', 'p9')
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
