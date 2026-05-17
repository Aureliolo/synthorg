import { http, HttpResponse } from 'msw'
import type {
  createSubworkflow,
  getVersion,
  listParents,
  listSubworkflows,
  listVersions,
  searchSubworkflows,
} from '@/api/endpoints/subworkflows'
import type { WorkflowDefinition } from '@/api/types/workflows'
import { buildWorkflow as buildDomainWorkflow } from './workflows'
import type { SubworkflowSummary } from '@/api/types/workflows'
import { apiError, emptyPage, paginatedFor, successFor, voidSuccess } from './helpers'

/**
 * Subworkflow-flavoured `buildWorkflow`. Delegates to the canonical
 * workflow builder and just flips `is_subworkflow` true so subworkflow
 * handlers return fixtures that still surface the boundary field that
 * distinguishes them from top-level workflows.
 */
export function buildSubworkflow(
  overrides: Partial<WorkflowDefinition> = {},
): WorkflowDefinition {
  // Spread overrides first, then force is_subworkflow=true so the returned
  // fixture always represents a subworkflow even if the caller accidentally
  // passed `is_subworkflow: false` in the override bag.
  return buildDomainWorkflow({ ...overrides, is_subworkflow: true })
}

export const subworkflowsHandlers = [
  http.get('/api/v1/subworkflows', () =>
    HttpResponse.json(
      paginatedFor<typeof listSubworkflows>(emptyPage<SubworkflowSummary>()),
    ),
  ),
  http.get('/api/v1/subworkflows/search', () =>
    HttpResponse.json(
      paginatedFor<typeof searchSubworkflows>(emptyPage<SubworkflowSummary>()),
    ),
  ),
  http.get('/api/v1/subworkflows/:id/versions', () =>
    HttpResponse.json(paginatedFor<typeof listVersions>(emptyPage<string>())),
  ),
  http.get('/api/v1/subworkflows/:id/versions/:version', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getVersion>(
        buildSubworkflow({
          id: String(params.id),
          version: String(params.version),
        }),
      ),
    ),
  ),
  http.get('/api/v1/subworkflows/:id/versions/:version/parents', () =>
    HttpResponse.json(paginatedFor<typeof listParents>(emptyPage())),
  ),
  http.post('/api/v1/subworkflows', async ({ request }) => {
    let body: unknown
    try {
      body = await request.json()
    } catch {
      return HttpResponse.json(apiError('Invalid JSON body'), { status: 400 })
    }
    const rawName = (body as { name?: unknown } | null)?.name
    if (!body || typeof body !== 'object' || typeof rawName !== 'string') {
      return HttpResponse.json(apiError("Field 'name' is required"), {
        status: 400,
      })
    }
    const name = rawName.trim()
    if (!name) {
      return HttpResponse.json(apiError("Field 'name' is required"), {
        status: 400,
      })
    }
    return HttpResponse.json(
      successFor<typeof createSubworkflow>(buildSubworkflow({ name })),
      { status: 201 },
    )
  }),
  http.delete('/api/v1/subworkflows/:id/versions/:version', () =>
    HttpResponse.json(voidSuccess()),
  ),
]
