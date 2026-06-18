import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { TaskDecomposeForm } from '@/pages/tasks/TaskDecomposeForm'
import { TaskDecomposeResult } from '@/pages/tasks/TaskDecomposeResult'
import type { SubtaskDraft } from '@/pages/tasks/useTaskDecomposeController'
import type { DecompositionResult } from '@/api/types'

function draft(overrides: Partial<SubtaskDraft> = {}): SubtaskDraft {
  return {
    key: 'draft-1',
    label: 'design',
    title: 'Design',
    description: 'Design it.',
    dependencies: '',
    ...overrides,
  }
}

describe('TaskDecomposeForm', () => {
  it('renders one subtask row and the action buttons', () => {
    render(
      <TaskDecomposeForm
        drafts={[draft()]}
        submitting={false}
        onChange={() => {}}
        onRemove={() => {}}
        onAdd={() => {}}
        onSubmit={() => {}}
      />,
    )
    expect(screen.getByText('Subtask 1')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add subtask' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Decompose' })).toBeInTheDocument()
  })

  it('invokes onAdd when the add button is clicked', async () => {
    const onAdd = vi.fn()
    render(
      <TaskDecomposeForm
        drafts={[draft()]}
        submitting={false}
        onChange={() => {}}
        onRemove={() => {}}
        onAdd={onAdd}
        onSubmit={() => {}}
      />,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Add subtask' }))
    expect(onAdd).toHaveBeenCalledOnce()
  })

  it('hides the remove control for a single subtask', () => {
    render(
      <TaskDecomposeForm
        drafts={[draft()]}
        submitting={false}
        onChange={() => {}}
        onRemove={() => {}}
        onAdd={() => {}}
        onSubmit={() => {}}
      />,
    )
    expect(screen.queryByRole('button', { name: /Remove subtask/ })).not.toBeInTheDocument()
  })
})

describe('TaskDecomposeResult', () => {
  function buildResult(): DecompositionResult {
    return {
      plan: {
        parent_task_id: 'parent-1',
        subtasks: [
          {
            id: 'sub-1',
            title: 'Design',
            description: 'Design it.',
            dependencies: [],
            estimated_complexity: 'medium',
            stakes: 'normal',
            required_skills: [],
            required_tags: [],
            required_role: null,
          },
        ],
        task_structure: 'sequential',
        coordination_topology: 'auto',
      },
      created_tasks: [],
      dependency_edges: [],
    }
  }

  it('renders the plan summary and subtasks', () => {
    render(<TaskDecomposeResult result={buildResult()} />)
    expect(screen.getByText('Structure')).toBeInTheDocument()
    expect(screen.getByText('Design')).toBeInTheDocument()
  })
})
