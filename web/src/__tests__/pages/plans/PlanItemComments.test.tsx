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
