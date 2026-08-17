import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { TaskFilterBar } from '@/pages/tasks/TaskFilterBar'
import type { TaskBoardFilters } from '@/utils/tasks'

const defaultProps = {
  filters: {} as TaskBoardFilters,
  onFiltersChange: vi.fn(),
  viewMode: 'board' as const,
  onViewModeChange: vi.fn(),
  onCreateTask: vi.fn(),
  assignees: [
    { id: 'agent-a', name: 'Agent A' },
    { id: 'agent-b', name: 'Agent B' },
  ],
  taskCount: 10,
}

describe('TaskFilterBar', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders all filter controls', () => {
    render(<TaskFilterBar {...defaultProps} />)
    expect(screen.getByLabelText('Filter by status')).toBeInTheDocument()
    expect(screen.getByLabelText('Filter by priority')).toBeInTheDocument()
    expect(screen.getByLabelText('Filter by assignee')).toBeInTheDocument()
    expect(screen.getByLabelText('Filter by type')).toBeInTheDocument()
    expect(screen.getByLabelText('Search tasks')).toBeInTheDocument()
  })

  it('renders task count', () => {
    render(<TaskFilterBar {...defaultProps} taskCount={24} />)
    expect(screen.getByText('24 tasks')).toBeInTheDocument()
  })

  it('renders singular for 1 task', () => {
    render(<TaskFilterBar {...defaultProps} taskCount={1} />)
    expect(screen.getByText('1 task')).toBeInTheDocument()
  })

  it('renders New Task button', () => {
    render(<TaskFilterBar {...defaultProps} />)
    expect(screen.getByText('New Task')).toBeInTheDocument()
  })

  it('calls onCreateTask when New Task button is clicked', async () => {
    const user = userEvent.setup()
    render(<TaskFilterBar {...defaultProps} />)
    await user.click(screen.getByText('New Task'))
    expect(defaultProps.onCreateTask).toHaveBeenCalledOnce()
  })

  it('renders view mode toggle buttons', () => {
    render(<TaskFilterBar {...defaultProps} />)
    expect(screen.getByLabelText('Board view')).toBeInTheDocument()
    expect(screen.getByLabelText('List view')).toBeInTheDocument()
  })

  it('calls onViewModeChange when toggle is clicked', async () => {
    const user = userEvent.setup()
    render(<TaskFilterBar {...defaultProps} />)
    await user.click(screen.getByLabelText('List view'))
    expect(defaultProps.onViewModeChange).toHaveBeenCalledWith('list')
  })

  it('calls onFiltersChange when status filter changes', async () => {
    const user = userEvent.setup()
    render(<TaskFilterBar {...defaultProps} />)
    await user.selectOptions(screen.getByLabelText('Filter by status'), 'in_progress')
    expect(defaultProps.onFiltersChange).toHaveBeenCalledWith({ status: 'in_progress' })
  })

  it('renders filter pills when filters are active', () => {
    render(<TaskFilterBar {...defaultProps} filters={{ status: 'in_progress', priority: 'high' }} />)
    expect(screen.getByText('Status: In Progress')).toBeInTheDocument()
    expect(screen.getByText('Priority: High')).toBeInTheDocument()
    expect(screen.getByText('Clear all')).toBeInTheDocument()
  })

  it('does not render filter pills when no filters are active', () => {
    render(<TaskFilterBar {...defaultProps} />)
    expect(screen.queryByText('Clear all')).not.toBeInTheDocument()
  })

  it('calls onFiltersChange with empty object when Clear all is clicked', async () => {
    const user = userEvent.setup()
    render(<TaskFilterBar {...defaultProps} filters={{ status: 'in_progress' }} />)
    await user.click(screen.getByText('Clear all'))
    expect(defaultProps.onFiltersChange).toHaveBeenCalledWith({})
  })
})
