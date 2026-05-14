import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { useWorkflowsStore } from '@/stores/workflows'
import { useToastStore } from '@/stores/toast'
import { apiError, apiSuccess, paginatedFor, voidSuccess } from '@/mocks/handlers'
import type { listWorkflows } from '@/api/endpoints/workflows'
import { server } from '@/test-setup'
import type { WorkflowDefinition } from '@/api/types/workflows'

function makeWorkflow(
  id: string,
  overrides?: Partial<WorkflowDefinition>,
): WorkflowDefinition {
  return {
    id,
    name: `wf-${id}`,
    description: null,
    workflow_type: 'sequential_pipeline',
    nodes: [],
    edges: [],
    created_at: '2026-04-01T00:00:00Z',
    updated_at: '2026-04-01T00:00:00Z',
    version: 1,
    ...overrides,
  } as WorkflowDefinition
}

function paginated(data: WorkflowDefinition[]) {
  return paginatedFor<typeof listWorkflows>({
    data,
    limit: 200,
    nextCursor: null,
    hasMore: false,
    pagination: {
      limit: 200,
      next_cursor: null,
      has_more: false,
    },
  })
}

function resetStore() {
  useWorkflowsStore.setState({
    workflows: [],
    totalWorkflows: 0,
    listLoading: false,
    listError: null,
    blueprints: [],
    blueprintsLoading: false,
    blueprintsError: null,
    searchQuery: '',
    workflowTypeFilter: null,
  })
  useToastStore.getState().dismissAll()
}

beforeEach(() => {
  resetStore()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('createWorkflow', () => {
  it('upserts the result and emits a success toast', async () => {
    const created = makeWorkflow('1', { name: 'Alpha' })
    server.use(
      http.post('/api/v1/workflows', () =>
        HttpResponse.json(apiSuccess(created), { status: 201 }),
      ),
    )

    const result = await useWorkflowsStore.getState().createWorkflow({
      name: 'Alpha',
      description: '',
      version: '1.0',
      workflow_type: 'sequential_pipeline',
      inputs: [],
      outputs: [],
      is_subworkflow: false,
      nodes: [],
      edges: [],
    })

    expect(result).toEqual(created)
    expect(useWorkflowsStore.getState().workflows[0]).toEqual(created)
    const toasts = useToastStore.getState().toasts
    expect(toasts).toHaveLength(1)
    expect(toasts[0]!.variant).toBe('success')
    expect(toasts[0]!.title).toContain('Alpha')
  })

  it('returns null and emits an error toast on API failure', async () => {
    server.use(
      http.post('/api/v1/workflows', () =>
        HttpResponse.json(apiError('boom')),
      ),
    )

    const result = await useWorkflowsStore.getState().createWorkflow({
      name: 'Alpha',
      description: '',
      version: '1.0',
      workflow_type: 'sequential_pipeline',
      inputs: [],
      outputs: [],
      is_subworkflow: false,
      nodes: [],
      edges: [],
    })

    expect(result).toBeNull()
    const toasts = useToastStore.getState().toasts
    expect(toasts).toHaveLength(1)
    expect(toasts[0]!.variant).toBe('error')
    expect(toasts[0]!.title).toBe('Failed to create workflow')
    expect(toasts[0]!.description).toBe('boom')
  })
})

describe('createFromBlueprint', () => {
  it('upserts the result and emits a success toast', async () => {
    const created = makeWorkflow('2', { name: 'Beta' })
    server.use(
      http.post('/api/v1/workflows/from-blueprint', () =>
        HttpResponse.json(apiSuccess(created), { status: 201 }),
      ),
    )

    const result = await useWorkflowsStore.getState().createFromBlueprint({
      blueprint_name: 'bp1',
      name: 'Beta',
    })

    expect(result).toEqual(created)
    const toasts = useToastStore.getState().toasts
    expect(toasts[0]!.variant).toBe('success')
    expect(toasts[0]!.title).toContain('Beta')
  })

  it('returns null on API failure', async () => {
    server.use(
      http.post('/api/v1/workflows/from-blueprint', () =>
        HttpResponse.json(apiError('boom')),
      ),
    )

    const result = await useWorkflowsStore.getState().createFromBlueprint({
      blueprint_name: 'bp1',
      name: 'Beta',
    })

    expect(result).toBeNull()
    const toasts = useToastStore.getState().toasts
    expect(toasts[0]!.variant).toBe('error')
    expect(toasts[0]!.title).toBe('Failed to create workflow from blueprint')
    expect(toasts[0]!.description).toBe('boom')
  })
})

describe('deleteWorkflow', () => {
  it('optimistically removes the workflow and returns true on success', async () => {
    const wf = makeWorkflow('1')
    useWorkflowsStore.setState({ workflows: [wf], totalWorkflows: 1 })
    server.use(
      http.delete('/api/v1/workflows/:id', () =>
        HttpResponse.json(voidSuccess()),
      ),
    )

    const result = await useWorkflowsStore.getState().deleteWorkflow('1')

    expect(result).toBe(true)
    expect(useWorkflowsStore.getState().workflows).toHaveLength(0)
    expect(useWorkflowsStore.getState().totalWorkflows).toBe(0)
    const toasts = useToastStore.getState().toasts
    expect(toasts[0]!.variant).toBe('success')
    expect(toasts[0]!.title).toBe('Workflow deleted')
  })

  it('rolls back state and returns false on API failure', async () => {
    const wf = makeWorkflow('1')
    useWorkflowsStore.setState({ workflows: [wf], totalWorkflows: 1 })
    server.use(
      http.delete('/api/v1/workflows/:id', () =>
        HttpResponse.json(apiError('boom')),
      ),
    )

    const result = await useWorkflowsStore.getState().deleteWorkflow('1')

    expect(result).toBe(false)
    expect(useWorkflowsStore.getState().workflows).toEqual([wf])
    expect(useWorkflowsStore.getState().totalWorkflows).toBe(1)
    const toasts = useToastStore.getState().toasts
    expect(toasts[0]!.variant).toBe('error')
    expect(toasts[0]!.title).toBe('Failed to delete workflow')
    expect(toasts[0]!.description).toBe('boom')
  })

  it('rollback preserves concurrent WS-triggered state updates', async () => {
    const wf1 = makeWorkflow('1')
    useWorkflowsStore.setState({ workflows: [wf1], totalWorkflows: 1 })

    server.use(
      http.delete('/api/v1/workflows/:id', async () => {
        // Simulate a WS-triggered upsert arriving during the in-flight
        // delete -- the store mutation happens before the delete's
        // rejection resolves.
        useWorkflowsStore.setState((s) => ({
          workflows: [makeWorkflow('2'), ...s.workflows],
          totalWorkflows: s.totalWorkflows + 1,
        }))
        return HttpResponse.json(apiError('boom'))
      }),
    )

    const result = await useWorkflowsStore.getState().deleteWorkflow('1')

    expect(result).toBe(false)
    const state = useWorkflowsStore.getState()
    expect(state.workflows.map((w) => w.id).sort()).toEqual(['1', '2'])
  })
})

describe('fetchWorkflows', () => {
  it('populates list and clears error on success', async () => {
    const items = [makeWorkflow('1'), makeWorkflow('2')]
    server.use(
      http.get('/api/v1/workflows', () =>
        HttpResponse.json(paginated(items)),
      ),
    )

    useWorkflowsStore.setState({ listError: 'stale' })
    await useWorkflowsStore.getState().fetchWorkflows()

    const state = useWorkflowsStore.getState()
    expect(state.workflows).toHaveLength(2)
    expect(state.totalWorkflows).toBe(2)
    expect(state.listLoading).toBe(false)
    expect(state.listError).toBeNull()
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })

  it('sets listError on failure without toasting (list-read pattern)', async () => {
    server.use(
      http.get('/api/v1/workflows', () =>
        HttpResponse.json(apiError('network down')),
      ),
    )

    await useWorkflowsStore.getState().fetchWorkflows()

    const state = useWorkflowsStore.getState()
    expect(state.listLoading).toBe(false)
    expect(state.listError).toBe('network down')
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })
})
