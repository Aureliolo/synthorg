import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import type { UseTaskBoardDataReturn } from '@/hooks/useTaskBoardData'
import TaskBoardPage from '@/pages/TaskBoardPage'
import { useTasksStore } from '@/stores/tasks'
import type { BulkDeleteOutcome } from '@/stores/_bulk-delete'
import { makeTask } from '../helpers/factories'

const defaultHookReturn: UseTaskBoardDataReturn = {
  tasks: [
    makeTask('t1', { status: 'assigned' }),
    makeTask('t2', { status: 'in_progress', title: 'Active task' }),
    makeTask('t3', { status: 'completed', title: 'Done task' }),
  ],
  selectedTask: null,
  total: 3,
  loading: false,
  loadingDetail: false,
  error: null,
  wsConnected: true,
  wsSetupError: null,
  fetchTask: vi.fn(),
  createTask: vi.fn(),
  updateTask: vi.fn(),
  transitionTask: vi.fn(),
  cancelTask: vi.fn(),
  deleteTask: vi.fn(),
  optimisticTransition: vi.fn(() => () => {}),
}

let hookReturn = { ...defaultHookReturn }

const getTaskBoardData = vi.fn(() => hookReturn)

vi.mock('@/hooks/useTaskBoardData', () => ({
  useTaskBoardData: () => getTaskBoardData(),
}))
vi.mock('@/hooks/useOptimisticUpdate', () => ({
  useOptimisticUpdate: () => ({ execute: vi.fn(), pending: false, error: null }),
}))

vi.mock('@xyflow/react', () => ({
  ReactFlow: () => null,
  Background: () => null,
  Controls: () => null,
  MarkerType: { ArrowClosed: 'arrowclosed' },
  Position: { Left: 'left', Right: 'right' },
}))

function renderBoard(initialEntries: string[] = ['/tasks']) {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <TaskBoardPage />
    </MemoryRouter>,
  )
}

describe('TaskBoardPage', () => {
  beforeEach(() => {
    hookReturn = { ...defaultHookReturn }
  })

  it('renders page heading', () => {
    renderBoard()
    expect(screen.getByText('Task Board')).toBeInTheDocument()
  })

  it('renders loading skeleton when loading with no data', () => {
    hookReturn = { ...defaultHookReturn, loading: true, tasks: [], total: 0 }
    renderBoard()
    expect(screen.getByLabelText('Loading task board')).toBeInTheDocument()
  })

  it('does not show skeleton when loading but data exists', () => {
    hookReturn = { ...defaultHookReturn, loading: true }
    renderBoard()
    expect(screen.getByText('Task Board')).toBeInTheDocument()
    expect(screen.queryByLabelText('Loading task board')).not.toBeInTheDocument()
  })

  it('renders filter bar', () => {
    renderBoard()
    expect(screen.getByLabelText('Filter by status')).toBeInTheDocument()
    expect(screen.getByLabelText('Filter by priority')).toBeInTheDocument()
  })

  it('renders task cards in board view', () => {
    renderBoard()
    expect(screen.getByText('Task t1')).toBeInTheDocument()
    expect(screen.getByText('Active task')).toBeInTheDocument()
    expect(screen.getByText('Done task')).toBeInTheDocument()
  })

  it('renders kanban columns', () => {
    const { container } = renderBoard()
    expect(container.querySelector('[data-column-id="ready"]')).toBeInTheDocument()
    expect(container.querySelector('[data-column-id="in_progress"]')).toBeInTheDocument()
    expect(container.querySelector('[data-column-id="done"]')).toBeInTheDocument()
  })

  it('renders New Task button', () => {
    renderBoard()
    expect(screen.getByText('New Task')).toBeInTheDocument()
  })

  it('shows error banner when error is set', () => {
    hookReturn = { ...defaultHookReturn, error: 'Failed to load tasks' }
    renderBoard()
    expect(screen.getByText('Failed to load tasks')).toBeInTheDocument()
  })

  it('shows WebSocket disconnect warning', () => {
    hookReturn = { ...defaultHookReturn, wsConnected: false }
    renderBoard()
    expect(screen.getByText(/disconnected/i)).toBeInTheDocument()
  })

  it('shows custom wsSetupError', () => {
    hookReturn = { ...defaultHookReturn, wsConnected: false, wsSetupError: 'WS auth failed' }
    renderBoard()
    expect(screen.getByText('WS auth failed')).toBeInTheDocument()
  })

  it('renders task count', () => {
    renderBoard()
    expect(screen.getByText('3 tasks')).toBeInTheDocument()
  })

  it('renders Show terminal checkbox', () => {
    renderBoard()
    expect(screen.getByText('Show terminal')).toBeInTheDocument()
  })
})

describe('clearing a selection of tasks', () => {
  beforeEach(() => {
    hookReturn = { ...defaultHookReturn }
  })

  it('offers no bulk bar until rows are picked', () => {
    renderBoard(['/tasks?view=list'])

    expect(screen.queryByRole('toolbar', { name: 'Task bulk actions' })).toBeNull()
  })

  it('deletes the picked rows in one call', async () => {
    // Deleting them one dialog at a time was the only way to clear a board:
    // 48 rows meant 48 dialogs, and the per-row endpoint is rate limited, so
    // a loop would have refused most of them anyway.
    const batchDeleteTasks = vi.fn(
      (ids: readonly string[]): Promise<BulkDeleteOutcome> =>
        Promise.resolve({ succeeded: ids.length, failed: 0, failedReasons: [] }),
    )
    useTasksStore.setState({ batchDeleteTasks })
    const user = userEvent.setup()
    renderBoard(['/tasks?view=list'])

    await user.click(screen.getByRole('checkbox', { name: 'Select task Task t1' }))
    await user.click(screen.getByRole('checkbox', { name: 'Select task Active task' }))
    await user.click(screen.getByRole('button', { name: 'Delete 2' }))
    await user.click(
      await screen.findByRole('button', { name: /^Delete 2$/, hidden: false }),
    )

    await waitFor(() => {
      expect(batchDeleteTasks).toHaveBeenCalledTimes(1)
    })
    expect(batchDeleteTasks.mock.calls[0]?.[0]).toHaveLength(2)
  })

  it('keeps the selection off the board view, where a card is a drag handle', () => {
    renderBoard(['/tasks'])

    expect(screen.queryByRole('checkbox', { name: /Select task/ })).toBeNull()
  })
})
