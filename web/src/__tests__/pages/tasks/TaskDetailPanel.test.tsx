import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { TaskDetailPanel } from '@/pages/tasks/TaskDetailPanel'
import type { Task } from '@/api/types/tasks'

const mockTask: Task = {
  id: 'task-1',
  title: 'Test task',
  description: 'Test description',
  type: 'development',
  status: 'in_progress',
  priority: 'high',
  project: 'test-project',
  created_by: 'agent-cto',
  assigned_to: 'agent-eng',
  reviewers: [],
  dependencies: ['dep-1'],
  artifacts_expected: [],
  acceptance_criteria: [
    { description: 'Criterion 1', met: true },
    { description: 'Criterion 2', met: false },
  ],
  estimated_complexity: 'complex',
  stakes: 'normal',
  budget_limit: 10,
  cost: 3.45,
  deadline: null,
  max_retries: 3,
  parent_task_id: null,
  delegation_chain: [],
  task_structure: null,
  coordination_topology: 'auto',
  middleware_override: null,
  source: null,
  metadata: {},
  hard_ceiling: null,
  forecast_id: null,
  version: 2,
  created_at: '2026-03-20T10:00:00Z',
  updated_at: '2026-03-25T14:00:00Z',
}

const noop = async () => {}
const noopSentinel = async () => true

describe('TaskDetailPanel', () => {
  it('renders task title', () => {
    render(<TaskDetailPanel task={mockTask} onClose={() => {}} onUpdate={noop} onTransition={noop} onCancel={noopSentinel} onDelete={noopSentinel} />)
    expect(screen.getByText('Test task')).toBeInTheDocument()
  })

  it('renders task description', () => {
    render(<TaskDetailPanel task={mockTask} onClose={() => {}} onUpdate={noop} onTransition={noop} onCancel={noopSentinel} onDelete={noopSentinel} />)
    expect(screen.getByText('Test description')).toBeInTheDocument()
  })

  it('renders status indicator with label', () => {
    render(<TaskDetailPanel task={mockTask} onClose={() => {}} onUpdate={noop} onTransition={noop} onCancel={noopSentinel} onDelete={noopSentinel} />)
    expect(screen.getByText('In Progress')).toBeInTheDocument()
  })

  it('renders priority badge and selector', () => {
    render(<TaskDetailPanel task={mockTask} onClose={() => {}} onUpdate={noop} onTransition={noop} onCancel={noopSentinel} onDelete={noopSentinel} />)
    // Priority appears in both badge and select dropdown
    expect(screen.getByLabelText('Change priority')).toHaveValue('high')
  })

  it('renders assignee', () => {
    render(<TaskDetailPanel task={mockTask} onClose={() => {}} onUpdate={noop} onTransition={noop} onCancel={noopSentinel} onDelete={noopSentinel} />)
    expect(screen.getByText('agent-eng')).toBeInTheDocument()
  })

  it('renders dependencies', () => {
    render(<TaskDetailPanel task={mockTask} onClose={() => {}} onUpdate={noop} onTransition={noop} onCancel={noopSentinel} onDelete={noopSentinel} />)
    expect(screen.getByText('dep-1')).toBeInTheDocument()
  })

  it('renders acceptance criteria', () => {
    render(<TaskDetailPanel task={mockTask} onClose={() => {}} onUpdate={noop} onTransition={noop} onCancel={noopSentinel} onDelete={noopSentinel} />)
    expect(screen.getByText('Criterion 1')).toBeInTheDocument()
    expect(screen.getByText('Criterion 2')).toBeInTheDocument()
  })

  it('renders available transition buttons', () => {
    render(<TaskDetailPanel task={mockTask} onClose={() => {}} onUpdate={noop} onTransition={noop} onCancel={noopSentinel} onDelete={noopSentinel} />)
    // in_progress can transition to in_review, failed, cancelled, interrupted
    expect(screen.getByRole('button', { name: 'In Review' })).toBeInTheDocument()
  })

  it('does not render transition buttons for completed tasks', () => {
    const completed = { ...mockTask, status: 'completed' as const }
    render(<TaskDetailPanel task={completed} onClose={() => {}} onUpdate={noop} onTransition={noop} onCancel={noopSentinel} onDelete={noopSentinel} />)
    expect(screen.queryByText('Transitions')).not.toBeInTheDocument()
  })

  it('calls onClose when close button is clicked', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(<TaskDetailPanel task={mockTask} onClose={onClose} onUpdate={noop} onTransition={noop} onCancel={noopSentinel} onDelete={noopSentinel} />)
    await user.click(screen.getByLabelText('Close panel'))
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('renders loading state', () => {
    render(<TaskDetailPanel task={mockTask} onClose={() => {}} onUpdate={noop} onTransition={noop} onCancel={noopSentinel} onDelete={noopSentinel} loading />)
    // Should not render task details when loading
    expect(screen.queryByText('Test description')).not.toBeInTheDocument()
  })

  it('renders Delete button', () => {
    render(<TaskDetailPanel task={mockTask} onClose={() => {}} onUpdate={noop} onTransition={noop} onCancel={noopSentinel} onDelete={noopSentinel} />)
    expect(screen.getByRole('button', { name: 'Delete' })).toBeInTheDocument()
  })

  it('renders Cancel Task button for non-terminal tasks', () => {
    render(<TaskDetailPanel task={mockTask} onClose={() => {}} onUpdate={noop} onTransition={noop} onCancel={noopSentinel} onDelete={noopSentinel} />)
    expect(screen.getByRole('button', { name: 'Cancel Task' })).toBeInTheDocument()
  })

  it('does not render Cancel Task button for cancelled tasks', () => {
    const cancelled = { ...mockTask, status: 'cancelled' as const }
    render(<TaskDetailPanel task={cancelled} onClose={() => {}} onUpdate={noop} onTransition={noop} onCancel={noopSentinel} onDelete={noopSentinel} />)
    expect(screen.queryByRole('button', { name: 'Cancel Task' })).not.toBeInTheDocument()
  })

  it('calls onTransition when transition button is clicked', async () => {
    const user = userEvent.setup()
    const onTransition = vi.fn().mockResolvedValue(undefined)
    render(<TaskDetailPanel task={mockTask} onClose={() => {}} onUpdate={noop} onTransition={onTransition} onCancel={noopSentinel} onDelete={noopSentinel} />)
    await user.click(screen.getByRole('button', { name: 'In Review' }))
    expect(onTransition).toHaveBeenCalledWith('task-1', { target_status: 'in_review', expected_version: 2 })
  })

  it('closes panel on Escape key', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(<TaskDetailPanel task={mockTask} onClose={onClose} onUpdate={noop} onTransition={noop} onCancel={noopSentinel} onDelete={noopSentinel} />)
    await user.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('calls onDelete after confirm dialog confirmation', async () => {
    const user = userEvent.setup()
    const onDelete = vi.fn().mockResolvedValue(true)
    const onClose = vi.fn()
    render(<TaskDetailPanel task={mockTask} onClose={onClose} onUpdate={noop} onTransition={noop} onCancel={noopSentinel} onDelete={onDelete} />)
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    // Confirm dialog should appear -- find the confirm button inside the dialog
    const dialog = screen.getByRole('alertdialog')
    const confirmButton = within(dialog).getByRole('button', { name: 'Delete' })
    await user.click(confirmButton)
    expect(onDelete).toHaveBeenCalledWith('task-1')
  })

  it('rejects cancel with empty reason', async () => {
    const user = userEvent.setup()
    const onCancel = vi.fn().mockResolvedValue(true)
    render(<TaskDetailPanel task={mockTask} onClose={() => {}} onUpdate={noop} onTransition={noop} onCancel={onCancel} onDelete={noopSentinel} />)
    await user.click(screen.getByRole('button', { name: 'Cancel Task' }))
    // Do NOT fill reason -- click confirm immediately
    const dialog = screen.getByRole('alertdialog')
    await user.click(within(dialog).getByRole('button', { name: 'Cancel Task' }))
    expect(onCancel).not.toHaveBeenCalled()
  })

  it('calls onCancel after confirm dialog confirmation', async () => {
    const user = userEvent.setup()
    const onCancel = vi.fn().mockResolvedValue(true)
    render(<TaskDetailPanel task={mockTask} onClose={() => {}} onUpdate={noop} onTransition={noop} onCancel={onCancel} onDelete={noopSentinel} />)
    await user.click(screen.getByRole('button', { name: 'Cancel Task' }))
    // Fill in reason
    const reasonInput = screen.getByLabelText('Cancellation reason')
    await user.type(reasonInput, 'No longer needed')
    // Confirm -- scope to the dialog to avoid ambiguity with footer button
    const dialog = screen.getByRole('alertdialog')
    await user.click(within(dialog).getByRole('button', { name: 'Cancel Task' }))
    expect(onCancel).toHaveBeenCalledWith('task-1', { reason: 'No longer needed' })
  })
})
