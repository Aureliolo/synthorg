import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { TaskCard } from '@/pages/tasks/TaskCard'
import type { Task } from '@/api/types/tasks'
import { makeTask as makeTaskFactory } from '../../helpers/factories'

function makeTask(overrides: Partial<Task> = {}): Task {
  return makeTaskFactory('task-1', {
    title: 'Test task',
    description: 'A test task description',
    ...overrides,
  })
}

describe('TaskCard', () => {
  it('renders task title', () => {
    render(<TaskCard task={makeTask()} onSelect={() => {}} />)
    expect(screen.getByText('Test task')).toBeInTheDocument()
  })

  it('renders description preview', () => {
    render(<TaskCard task={makeTask()} onSelect={() => {}} />)
    expect(screen.getByText('A test task description')).toBeInTheDocument()
  })

  it('renders assignee avatar when assigned_to is set', () => {
    render(<TaskCard task={makeTask()} onSelect={() => {}} />)
    expect(screen.getByLabelText('Engineer')).toBeInTheDocument()
  })

  it('does not render avatar when assigned_to is null', () => {
    render(<TaskCard task={makeTask({ assigned_to: null })} onSelect={() => {}} />)
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })

  it('renders priority badge', () => {
    render(<TaskCard task={makeTask({ priority: 'critical' })} onSelect={() => {}} />)
    expect(screen.getByText('Critical')).toBeInTheDocument()
  })

  it('renders dependency count when dependencies exist', () => {
    render(<TaskCard task={makeTask({ dependencies: ['dep-1', 'dep-2'] })} onSelect={() => {}} />)
    expect(screen.getByText('2')).toBeInTheDocument()
  })

  it('does not render dependency count when empty', () => {
    render(<TaskCard task={makeTask({ dependencies: [] })} onSelect={() => {}} />)
    expect(screen.queryByTitle(/dependencies/)).not.toBeInTheDocument()
  })

  it('calls onSelect when clicked', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(<TaskCard task={makeTask()} onSelect={onSelect} />)
    await user.click(screen.getByRole('button'))
    expect(onSelect).toHaveBeenCalledWith('task-1')
  })

  it('calls onSelect on Enter key', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(<TaskCard task={makeTask()} onSelect={onSelect} />)
    const card = screen.getByRole('button')
    card.focus()
    await user.keyboard('{Enter}')
    expect(onSelect).toHaveBeenCalledWith('task-1')
  })

  it('has accessible label with task title', () => {
    render(<TaskCard task={makeTask({ title: 'My task' })} onSelect={() => {}} />)
    expect(screen.getByLabelText('Task: My task')).toBeInTheDocument()
  })

  it('renders cost when cost is set and > 0', () => {
    render(<TaskCard task={makeTask({ cost: 5.25 })} onSelect={() => {}} />)
    expect(screen.getByText(/5\.25/)).toBeInTheDocument()
  })

  it('does not render cost when cost is 0', () => {
    render(<TaskCard task={makeTask({ cost: 0 })} onSelect={() => {}} />)
    // Cost element should not be present since cost is 0. Currency
    // symbol is whatever ``DEFAULT_CURRENCY`` resolves to at render
    // time, so we assert on the numeric formatting rather than a
    // fixed symbol to keep this currency-agnostic.
    expect(screen.queryByText(/0\.00/)).not.toBeInTheDocument()
  })

  it('renders deadline when set', () => {
    render(<TaskCard task={makeTask({ deadline: '2099-01-01T00:00:00.000Z' })} onSelect={() => {}} />)
    // Should render some deadline text (relative time)
    expect(screen.getByTitle(/Deadline/)).toBeInTheDocument()
  })
})
