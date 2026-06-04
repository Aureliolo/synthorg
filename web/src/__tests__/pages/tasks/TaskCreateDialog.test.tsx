import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { TaskCreateDialog } from '@/pages/tasks/TaskCreateDialog'
import type { TaskBoardSubmissionResponse } from '@/api/types/tasks'

// onCreate returns ``TaskBoardSubmissionResponse | null`` per the
// 202-Accepted sentinel-return contract; this stub resolves to null
// which the dialog treats as failure (keeps the dialog open). Tests
// that assert the call site override with their own mock.
const nullCreate = async (): Promise<TaskBoardSubmissionResponse | null> => null

const makeSubmission = (
  overrides: Partial<TaskBoardSubmissionResponse> = {},
): TaskBoardSubmissionResponse => ({
  correlation_id: 'corr-test',
  title: 'My task',
  project: 'proj',
  status: 'submitted',
  ...overrides,
})

describe('TaskCreateDialog', () => {
  it('renders nothing when closed', () => {
    render(<TaskCreateDialog open={false} onOpenChange={() => {}} onCreate={nullCreate} />)
    expect(screen.queryByText('New Task')).not.toBeInTheDocument()
  })

  it('renders dialog when open', () => {
    render(<TaskCreateDialog open={true} onOpenChange={() => {}} onCreate={nullCreate} />)
    expect(screen.getByText('New Task')).toBeInTheDocument()
  })

  it('renders required form fields', () => {
    render(<TaskCreateDialog open={true} onOpenChange={() => {}} onCreate={nullCreate} />)
    expect(screen.getByPlaceholderText('Task title')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Describe the task...')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Project name')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Agent or user')).toBeInTheDocument()
  })

  it('shows validation errors for empty required fields', async () => {
    const user = userEvent.setup()
    render(<TaskCreateDialog open={true} onOpenChange={() => {}} onCreate={nullCreate} />)
    await user.click(screen.getByText('Create Task'))
    expect(screen.getByText('Title is required')).toBeInTheDocument()
    expect(screen.getByText('Description is required')).toBeInTheDocument()
    expect(screen.getByText('Project is required')).toBeInTheDocument()
    expect(screen.getByText('Creator is required')).toBeInTheDocument()
  })

  it('calls onCreate with form data on valid submission', async () => {
    const user = userEvent.setup()
    const onCreate = vi.fn().mockResolvedValue(makeSubmission())
    render(<TaskCreateDialog open={true} onOpenChange={() => {}} onCreate={onCreate} />)

    await user.type(screen.getByPlaceholderText('Task title'), 'My task')
    await user.type(screen.getByPlaceholderText('Describe the task...'), 'Task description')
    await user.type(screen.getByPlaceholderText('Project name'), 'my-project')
    await user.type(screen.getByPlaceholderText('Agent or user'), 'agent-cto')

    await user.click(screen.getByText('Create Task'))

    expect(onCreate).toHaveBeenCalledWith(
      expect.objectContaining({
        title: 'My task',
        description: 'Task description',
        project: 'my-project',
        created_by: 'agent-cto',
      }),
    )
  })

  it('keeps dialog open and preserves form state on sentinel-null failure', async () => {
    // Store-owned error UX: on failure the store emits a toast and
    // returns ``null``. The dialog MUST keep itself open and preserve
    // the user's inputs so they can retry without re-typing.
    const user = userEvent.setup()
    const onOpenChange = vi.fn()
    const onCreate = vi.fn().mockResolvedValue(null)
    render(<TaskCreateDialog open={true} onOpenChange={onOpenChange} onCreate={onCreate} />)

    await user.type(screen.getByPlaceholderText('Task title'), 'My task')
    await user.type(screen.getByPlaceholderText('Describe the task...'), 'Desc')
    await user.type(screen.getByPlaceholderText('Project name'), 'proj')
    await user.type(screen.getByPlaceholderText('Agent or user'), 'agent')

    await user.click(screen.getByText('Create Task'))
    expect(onCreate).toHaveBeenCalled()
    expect(onOpenChange).not.toHaveBeenCalledWith(false)
    expect(screen.getByPlaceholderText<HTMLInputElement>('Task title').value).toBe('My task')
  })

  it('calls onOpenChange(false) on successful creation', async () => {
    const user = userEvent.setup()
    const onOpenChange = vi.fn()
    const onCreate = vi.fn().mockResolvedValue(makeSubmission())
    render(<TaskCreateDialog open={true} onOpenChange={onOpenChange} onCreate={onCreate} />)

    await user.type(screen.getByPlaceholderText('Task title'), 'My task')
    await user.type(screen.getByPlaceholderText('Describe the task...'), 'Desc')
    await user.type(screen.getByPlaceholderText('Project name'), 'proj')
    await user.type(screen.getByPlaceholderText('Agent or user'), 'agent')

    await user.click(screen.getByText('Create Task'))
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })
})
