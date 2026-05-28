import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useWorkflowsStore } from '@/stores/workflows'
import { useToastStore } from '@/stores/toast'
import { downloadTextFile } from '@/utils/download'
import { server } from '@/test-setup'
import type { WorkflowDefinition } from '@/api/types/workflows'

// The store action drives a browser download via downloadTextFile, which
// relies on URL.createObjectURL (absent in jsdom). Mock the helper so the
// test asserts the contract (content + filename) without a real anchor.
vi.mock('@/utils/download', () => ({
  downloadTextFile: vi.fn(),
  downloadArtifactFile: vi.fn(),
}))

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

describe('useWorkflowsStore.exportWorkflow', () => {
  beforeEach(() => {
    useWorkflowsStore.setState({
      workflows: [],
      totalWorkflows: 0,
      nextCursor: null,
      hasMore: false,
      listLoading: false,
      listError: null,
    })
    useToastStore.getState().dismissAll()
    vi.mocked(downloadTextFile).mockClear()
  })

  it('downloads the exported YAML and toasts on success', async () => {
    useWorkflowsStore.setState({
      workflows: [makeWorkflow('1', { name: 'My Workflow' })],
      totalWorkflows: 1,
    })
    server.use(
      http.post('/api/v1/workflows/:id/export', () =>
        new HttpResponse('name: My Workflow\n', {
          headers: { 'Content-Type': 'text/yaml' },
        }),
      ),
    )

    const result = await useWorkflowsStore.getState().exportWorkflow('1')

    expect(result).toBe(true)
    expect(downloadTextFile).toHaveBeenCalledWith(
      'name: My Workflow\n',
      'My Workflow.yaml',
      'text/yaml',
    )
    expect(useToastStore.getState().toasts[0]!.variant).toBe('success')
  })

  it('falls back to a generic filename when the workflow is not in the list', async () => {
    server.use(
      http.post('/api/v1/workflows/:id/export', () =>
        new HttpResponse('name: ghost\n', {
          headers: { 'Content-Type': 'text/yaml' },
        }),
      ),
    )

    await useWorkflowsStore.getState().exportWorkflow('missing')

    expect(downloadTextFile).toHaveBeenCalledWith(
      'name: ghost\n',
      'workflow.yaml',
      'text/yaml',
    )
  })

  it('toasts an error and does not download on failure', async () => {
    useWorkflowsStore.setState({
      workflows: [makeWorkflow('1', { name: 'My Workflow' })],
      totalWorkflows: 1,
    })
    server.use(
      http.post('/api/v1/workflows/:id/export', () =>
        HttpResponse.text('boom', { status: 500 }),
      ),
    )

    const result = await useWorkflowsStore.getState().exportWorkflow('1')

    expect(result).toBe(false)
    expect(downloadTextFile).not.toHaveBeenCalled()
    const toasts = useToastStore.getState().toasts
    expect(toasts[0]!.variant).toBe('error')
    expect(toasts[0]!.title).toBe('Failed to export workflow')
  })
})
