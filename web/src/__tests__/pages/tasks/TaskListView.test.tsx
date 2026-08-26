import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { TaskListView } from '@/pages/tasks/TaskListView'
import { makeTask } from '../../helpers/factories'

describe('TaskListView', () => {
  const tasks = [
    makeTask('t1', 'First task', { status: 'in_progress' }),
    makeTask('t2', 'Second task', { status: 'completed', assigned_to: null }),
  ]

  it('renders table headers', () => {
    render(<TaskListView tasks={tasks} onSelectTask={() => {}} />)
    expect(screen.getByText('Status')).toBeInTheDocument()
    expect(screen.getByText('Title')).toBeInTheDocument()
    expect(screen.getByText('Assignee')).toBeInTheDocument()
    expect(screen.getByText('Priority')).toBeInTheDocument()
  })

  it('renders task rows', () => {
    render(<TaskListView tasks={tasks} onSelectTask={() => {}} />)
    expect(screen.getByText('First task')).toBeInTheDocument()
    expect(screen.getByText('Second task')).toBeInTheDocument()
  })

  it('shows Unassigned for tasks without assignee', () => {
    render(<TaskListView tasks={tasks} onSelectTask={() => {}} />)
    expect(screen.getByText('Unassigned')).toBeInTheDocument()
  })

  it('calls onSelectTask when row is clicked', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(<TaskListView tasks={tasks} onSelectTask={onSelect} />)
    await user.click(screen.getByText('First task'))
    expect(onSelect).toHaveBeenCalledWith('t1')
  })

  it('calls onSelectTask on Enter key', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(<TaskListView tasks={tasks} onSelectTask={onSelect} />)
    const row = screen.getByLabelText('Task: First task')
    row.focus()
    await user.keyboard('{Enter}')
    expect(onSelect).toHaveBeenCalledWith('t1')
  })

  it('calls onSelectTask on Space key', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(<TaskListView tasks={tasks} onSelectTask={onSelect} />)
    const row = screen.getByLabelText('Task: First task')
    row.focus()
    await user.keyboard(' ')
    expect(onSelect).toHaveBeenCalledWith('t1')
  })

  it('renders empty state when no tasks', () => {
    render(<TaskListView tasks={[]} onSelectTask={() => {}} />)
    expect(screen.getByText('No tasks yet')).toBeInTheDocument()
    // Nothing here knows whether a filter emptied the list, so it must not
    // name filters: with none set, "adjust your filters" points an operator at
    // something they cannot act on.
    expect(screen.queryByText(/filters/i)).not.toBeInTheDocument()
  })

  it('defers to the page when it knows why the list is empty', () => {
    render(
      <TaskListView
        tasks={[]}
        onSelectTask={() => {}}
        emptyNode={<p>No tasks match your filters</p>}
      />,
    )

    expect(screen.getByText('No tasks match your filters')).toBeInTheDocument()
    expect(screen.queryByText('No tasks yet')).not.toBeInTheDocument()
  })
})
