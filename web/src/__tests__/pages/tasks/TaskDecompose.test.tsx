import { act, render, renderHook, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { apiError } from '@/mocks/handlers'
import { server } from '@/test-setup'
import { TaskDecomposeForm } from '@/pages/tasks/TaskDecomposeForm'
import { TaskDecomposeResult } from '@/pages/tasks/TaskDecomposeResult'
import {
  useTaskDecomposeController,
  type SubtaskDraft,
} from '@/pages/tasks/useTaskDecomposeController'
import type { DecompositionResult } from '@/api/types/decomposition'
import { useToastStore } from '@/stores/toast'

function draft(overrides: Partial<SubtaskDraft> = {}): SubtaskDraft {
  return {
    key: 'draft-1',
    label: 'design',
    title: 'Design',
    description: 'Design it.',
    dependencies: '',
    acceptanceCriteria: 'the design is reviewed',
    expectedArtifacts: 'docs/design.md',
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

  it('disables Decompose while a required field is missing', () => {
    render(
      <TaskDecomposeForm
        drafts={[draft()]}
        submitting={false}
        canSubmit={false}
        onChange={() => {}}
        onRemove={() => {}}
        onAdd={() => {}}
        onSubmit={() => {}}
      />,
    )
    expect(screen.getByRole('button', { name: 'Decompose' })).toBeDisabled()
  })
})

describe('useTaskDecomposeController', () => {
  it('refuses to submit a subtask that declares no deliverable', () => {
    const { result } = renderHook(() => useTaskDecomposeController('task-1'))

    // The empty initial draft is exactly the shape the backend rejects.
    expect(result.current.canSubmit).toBe(false)
  })

  it('allows submit once every required field is filled', () => {
    const { result } = renderHook(() => useTaskDecomposeController('task-1'))

    act(() => {
      result.current.updateDraft(0, {
        label: 'design',
        title: 'Design',
        description: 'Design it.',
        acceptanceCriteria: 'the design is reviewed',
        expectedArtifacts: 'docs/design.md',
      })
    })

    expect(result.current.canSubmit).toBe(true)
  })

  it('surfaces the backend detail when the deliverable guard rejects', async () => {
    // Scoped override: assert the surfaced detail against a handler this test
    // owns, not the global default's incidental rejection shape.
    server.use(
      http.post('/api/v1/tasks/:id/decompose', () =>
        HttpResponse.json(
          apiError("Field 'expected_artifacts' must be non-empty"),
          { status: 422 },
        ),
      ),
    )
    const { result } = renderHook(() => useTaskDecomposeController('task-1'))
    act(() => {
      result.current.updateDraft(0, {
        label: 'design',
        title: 'Design',
        description: 'Design it.',
        acceptanceCriteria: 'the design is reviewed',
        // Submitted anyway: the guard has to hold even if the form is bypassed.
        expectedArtifacts: '',
      })
    })

    await act(async () => {
      await result.current.submit()
    })

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts
      expect(toasts.at(-1)?.variant).toBe('error')
      expect(toasts.at(-1)?.description).toContain('expected_artifacts')
    })
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
            expected_artifacts: [],
            acceptance_criteria: [],
            satisfies: [],
            kind: 'work',
            options: [],
          },
        ],
        open_questions: [],
        assumptions: [],
        planning_strategy: null,
        task_structure: 'sequential',
        coordination_topology: 'auto',
      },
      created_tasks: [],
      dependency_edges: [],
      depth: 0,
      children: [],
    }
  }

  it('renders the plan summary and subtasks', () => {
    render(<TaskDecomposeResult result={buildResult()} />)
    expect(screen.getByText('Structure')).toBeInTheDocument()
    expect(screen.getByText('Design')).toBeInTheDocument()
  })
})
