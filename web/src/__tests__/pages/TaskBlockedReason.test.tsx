import { render, screen } from '@testing-library/react'
import { TaskCard } from '@/pages/tasks/TaskCard'
import { makeTask } from '../helpers/factories'

/**
 * A blocked card says what it is waiting on.
 *
 * The invariant: the reasons want opposite actions from an operator (fill a
 * role, decide something, replan, or nothing at all because a run stopped),
 * and the board is where they find out something stopped. Rendering only the
 * status there means opening every blocked card to learn which wait it is.
 */
describe('a blocked task card shows its reason', () => {
  it('names the reason on the card', () => {
    render(
      <TaskCard
        task={makeTask('t1', {
          status: 'blocked',
          blocked_reason: 'reviewer_unstaffed',
        })}
        onSelect={vi.fn()}
      />,
    )
    expect(screen.getByText(/Completion Reviewer/)).toBeInTheDocument()
  })

  it('tells the two staffing waits apart', () => {
    // Filling one role releases nothing parked on the other, so a card that
    // blurred them would send the operator after the wrong hire.
    render(
      <TaskCard
        task={makeTask('t1', {
          status: 'blocked',
          blocked_reason: 'red_team_unstaffed',
        })}
        onSelect={vi.fn()}
      />,
    )
    expect(screen.getByText(/Red Team/)).toBeInTheDocument()
  })

  it('says nothing when the task is not blocked', () => {
    const { container } = render(
      <TaskCard
        task={makeTask('t1', { status: 'in_progress', blocked_reason: null })}
        onSelect={vi.fn()}
      />,
    )
    expect(container.querySelector('.text-warning')).toBeNull()
  })
})
