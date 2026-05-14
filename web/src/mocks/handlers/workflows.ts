import { http, HttpResponse } from 'msw'
import type {
  createFromBlueprint,
  createWorkflow,
  getWorkflow,
  getWorkflowDiff,
  getWorkflowVersion,
  listBlueprints,
  listWorkflowVersions,
  listWorkflows,
  rollbackWorkflow,
  updateWorkflow,
  validateWorkflow,
  validateWorkflowDraft,
} from '@/api/endpoints/workflows'
import type {
  WorkflowDefinition,
  WorkflowDefinitionVersionSummary,
} from '@/api/types/workflows'
import { emptyPage, paginatedFor, successFor, voidSuccess } from './helpers'

const NOW = '2026-04-19T00:00:00Z'

export function buildWorkflow(
  overrides: Partial<WorkflowDefinition> = {},
): WorkflowDefinition {
  return {
    id: 'workflow-default',
    name: 'Default Workflow',
    description: '',
    workflow_type: 'sequential_pipeline',
    version: '1',
    inputs: [],
    outputs: [],
    is_subworkflow: false,
    nodes: [],
    edges: [],
    created_by: 'user-1',
    created_at: NOW,
    updated_at: NOW,
    revision: 1,
    ...overrides,
  }
}

export const workflowsHandlers = [
  http.get('/api/v1/workflows', () =>
    HttpResponse.json(
      paginatedFor<typeof listWorkflows>(emptyPage<WorkflowDefinition>()),
    ),
  ),
  http.post('/api/v1/workflows/validate-draft', () =>
    HttpResponse.json(
      successFor<typeof validateWorkflowDraft>({ valid: true, errors: [] }),
    ),
  ),
  http.get('/api/v1/workflows/blueprints', () =>
    HttpResponse.json(successFor<typeof listBlueprints>([])),
  ),
  http.post('/api/v1/workflows/from-blueprint', async ({ request }) => {
    const body = (await request.json()) as { name?: string }
    return HttpResponse.json(
      successFor<typeof createFromBlueprint>(
        buildWorkflow({ name: body.name ?? 'from-blueprint' }),
      ),
      { status: 201 },
    )
  }),
  http.get('/api/v1/workflows/:id', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getWorkflow>(buildWorkflow({ id: String(params.id) })),
    ),
  ),
  http.post('/api/v1/workflows', async ({ request }) => {
    const body = (await request.json()) as { name?: string }
    return HttpResponse.json(
      successFor<typeof createWorkflow>(
        buildWorkflow({ name: body.name ?? 'new-workflow' }),
      ),
      { status: 201 },
    )
  }),
  http.patch('/api/v1/workflows/:id', async ({ params, request }) => {
    const body = (await request.json()) as Partial<WorkflowDefinition>
    return HttpResponse.json(
      successFor<typeof updateWorkflow>(
        buildWorkflow({ ...body, id: String(params.id) }),
      ),
    )
  }),
  http.delete('/api/v1/workflows/:id', () => HttpResponse.json(voidSuccess())),
  http.post('/api/v1/workflows/:id/validate', () =>
    HttpResponse.json(
      successFor<typeof validateWorkflow>({ valid: true, errors: [] }),
    ),
  ),
  http.post('/api/v1/workflows/:id/export', () =>
    new HttpResponse('# default exported workflow YAML', {
      headers: { 'Content-Type': 'text/plain' },
    }),
  ),
  http.get('/api/v1/workflows/:id/versions', () =>
    HttpResponse.json(
      paginatedFor<typeof listWorkflowVersions>(
        emptyPage<WorkflowDefinitionVersionSummary>(),
      ),
    ),
  ),
  http.get('/api/v1/workflows/:id/versions/:version', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getWorkflowVersion>({
        entity_id: String(params.id),
        version: Number(params.version),
        content_hash: 'sha256:0',
        snapshot: {
          id: String(params.id),
          name: 'snapshot',
          description: '',
          workflow_type: 'sequential_pipeline',
          nodes: [],
          edges: [],
          created_by: 'user-1',
        },
        saved_by: 'user-1',
        saved_at: NOW,
      }),
    ),
  ),
  http.get('/api/v1/workflows/:id/diff', ({ params, request }) => {
    const url = new URL(request.url)
    return HttpResponse.json(
      successFor<typeof getWorkflowDiff>({
        definition_id: String(params.id),
        from_version: Number(url.searchParams.get('from_version') ?? 1),
        to_version: Number(url.searchParams.get('to_version') ?? 2),
        node_changes: [],
        edge_changes: [],
        metadata_changes: [],
        summary: '',
      }),
    )
  }),
  http.post('/api/v1/workflows/:id/rollback', ({ params }) =>
    HttpResponse.json(
      successFor<typeof rollbackWorkflow>(buildWorkflow({ id: String(params.id) })),
    ),
  ),
]
