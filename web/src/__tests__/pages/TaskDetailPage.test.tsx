import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { http, HttpResponse } from 'msw'
import { useTasksStore } from '@/stores/tasks'
import { apiError, apiSuccess } from '@/mocks/handlers'
import { server } from '@/test-setup'
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
  dependencies: [],
  artifacts_expected: [],
  acceptance_criteria: [],
  estimated_complexity: 'medium',
  stakes: 'normal',
  budget_limit: 10,
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
  version: 1,
  created_at: '2026-03-20T10:00:00Z',
  updated_at: '2026-03-25T14:00:00Z',
}

type FetchMode =
  | { kind: 'resolve'; task: Task }
  | { kind: 'error'; message: string }

const fetchState: { mode: FetchMode } = {
  mode: { kind: 'resolve', task: mockTask },
}

function installTaskHandler() {
  server.use(
    http.get('/api/v1/tasks/:id', () => {
      const mode = fetchState.mode
      if (mode.kind === 'error') {
        return HttpResponse.json(apiError(mode.message))
      }
      return HttpResponse.json(apiSuccess(mode.task))
    }),
    http.get('/api/v1/tasks', () =>
      HttpResponse.json({
        data: [],
        error: null,
        error_detail: null,
        success: true,
        pagination: { total: 0, offset: 0, limit: 200 },
      }),
    ),
  )
}

function resetStore(
  overrides: Partial<{
    selectedTask: Task | null
    loadingDetail: boolean
    error: string | null
  }> = {},
) {
  useTasksStore.setState({
    tasks: [],
    selectedTask: null,
    total: 0,
    loading: false,
    loadingDetail: false,
    error: null,
    ...overrides,
  })
}

async function renderDetailPage() {
  const { default: TaskDetailPage } = await import('@/pages/TaskDetailPage')
  return render(
    <MemoryRouter initialEntries={['/tasks/task-1']}>
      <Routes>
        <Route path="/tasks/:taskId" element={<TaskDetailPage />} />
        <Route path="/tasks" element={<div>Board</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('TaskDetailPage', () => {
  const pendingReleasers: Array<() => void> = []
  const pendingHandlerPromises: Array<Promise<unknown>> = []

  beforeEach(() => {
    resetStore()
    fetchState.mode = { kind: 'resolve', task: mockTask }
    installTaskHandler()
  })

  afterEach(async () => {
    // Release any gated handlers and await their resolution so handler
    // continuations cannot mutate `useTasksStore` after the next test's
    // reset has already run.
    for (const release of pendingReleasers.splice(0)) {
      release()
    }
    await Promise.all(pendingHandlerPromises.splice(0))
  })

  it('renders loading spinner when loadingDetail is true', async () => {
    let release!: () => void
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    pendingReleasers.push(release)
    server.use(
      http.get('/api/v1/tasks/:id', () => {
        const handled = (async () => {
          await gate
          return HttpResponse.json(apiSuccess(mockTask))
        })()
        pendingHandlerPromises.push(handled)
        return handled
      }),
    )
    resetStore({ loadingDetail: true })
    await renderDetailPage()
    expect(
      screen.getByRole('status', { name: 'Loading task' }),
    ).toBeInTheDocument()
    expect(screen.queryByText('Test task')).not.toBeInTheDocument()
  })

  it('renders loading spinner when task is null', async () => {
    let release!: () => void
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    pendingReleasers.push(release)
    server.use(
      http.get('/api/v1/tasks/:id', () => {
        const handled = (async () => {
          await gate
          return HttpResponse.json(apiSuccess(mockTask))
        })()
        pendingHandlerPromises.push(handled)
        return handled
      }),
    )
    resetStore({ selectedTask: null, loadingDetail: false })
    await renderDetailPage()
    expect(
      screen.getByRole('status', { name: 'Loading task' }),
    ).toBeInTheDocument()
    expect(screen.queryByText('Test task')).not.toBeInTheDocument()
  })

  it('renders error message when fetch fails', async () => {
    server.use(
      http.get('/api/v1/tasks/:id', () =>
        HttpResponse.json(apiError('Task not found')),
      ),
    )
    await renderDetailPage()
    expect(await screen.findByText('Task not found')).toBeInTheDocument()
  })

  it('renders task details when task is loaded', async () => {
    await renderDetailPage()
    // The task title appears in both the breadcrumb and the detail header; just confirm
    // at least one instance is present plus the description.
    const titleMatches = await screen.findAllByText('Test task')
    expect(titleMatches.length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('Test description')).toBeInTheDocument()
  })

  it('renders breadcrumb link to task board', async () => {
    await renderDetailPage()
    const link = await screen.findByRole('link', { name: 'Tasks' })
    // Assert destination too so an accidental "Tasks" link elsewhere on
    // the page does not silently satisfy the test.
    expect(link).toHaveAttribute('href', '/tasks')
  })

  it('renders transition buttons for in_progress task', async () => {
    await renderDetailPage()
    expect(
      await screen.findByRole('button', { name: 'In Review' }),
    ).toBeInTheDocument()
  })

  it('renders Delete button', async () => {
    await renderDetailPage()
    expect(
      await screen.findByRole('button', { name: 'Delete' }),
    ).toBeInTheDocument()
  })

  it('renders Cancel Task button for non-terminal tasks', async () => {
    await renderDetailPage()
    expect(
      await screen.findByRole('button', { name: 'Cancel Task' }),
    ).toBeInTheDocument()
  })

  it('does not render Cancel Task button for completed tasks', async () => {
    server.use(
      http.get('/api/v1/tasks/:id', () =>
        HttpResponse.json(apiSuccess({ ...mockTask, status: 'completed' })),
      ),
    )
    await renderDetailPage()
    await waitFor(() => expect(screen.getAllByText('Test task').length).toBeGreaterThanOrEqual(1))
    expect(
      screen.queryByRole('button', { name: 'Cancel Task' }),
    ).not.toBeInTheDocument()
  })
})
