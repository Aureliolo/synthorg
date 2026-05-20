import { http, HttpResponse } from 'msw'
import type {
  getProjectDoc,
  getProjectDocHistory,
  listProjectDocs,
  searchProjectDocs,
  DocSummary,
  LivingDocument,
  DocVersion,
  DocSearchHit,
} from '@/api/endpoints/projectDocs'
import type { PaginationMeta } from '@/api/types/http'
import {
  apiSuccess,
  emptyPage,
  paginatedFor,
  successFor,
} from './helpers'

function buildSummary(overrides: Partial<DocSummary> = {}): DocSummary {
  return {
    project_id: 'proj-default',
    slug: 'doc-default',
    title: 'Default doc',
    doc_type: 'status_report',
    tags: [],
    updated_at: '2026-05-20T00:00:00Z',
    ...overrides,
  }
}

function buildDoc(overrides: Partial<LivingDocument> = {}): LivingDocument {
  return {
    slug: 'doc-default',
    title: 'Default doc',
    doc_type: 'status_report',
    tags: [],
    related_task_ids: [],
    author_agent_id: 'agent-default',
    body: [
      {
        block_kind: 'heading',
        block_id: 'block-default-1',
        level: 2,
        text: 'Summary',
      },
      {
        block_kind: 'prose',
        block_id: 'block-default-2',
        text: 'Default doc body.',
      },
    ],
    created_at: '2026-05-20T00:00:00Z',
    updated_at: '2026-05-20T00:00:00Z',
    ...overrides,
  }
}

const pagination: PaginationMeta = {
  limit: 50,
  next_cursor: null,
  has_more: false,
}

// Storybook-facing populated list.
export const projectDocsList = [
  http.get('/api/v1/projects/:projectId/docs', ({ params }) => {
    const projectId = String(params.projectId)
    const summaries: DocSummary[] = [
      buildSummary({
        project_id: projectId,
        slug: 'q2-status',
        title: 'Q2 status report',
        doc_type: 'status_report',
        tags: ['checkout', 'q2'],
      }),
      buildSummary({
        project_id: projectId,
        slug: 'product-prd',
        title: 'Product PRD',
        doc_type: 'deliverable',
        tags: ['product'],
      }),
    ]
    return HttpResponse.json({
      data: summaries,
      error: null,
      error_detail: null,
      success: true,
      pagination,
    })
  }),
  http.get('/api/v1/projects/:projectId/docs/:slug', ({ params }) =>
    HttpResponse.json(
      apiSuccess(
        buildDoc({
          slug: String(params.slug),
          title: `Doc ${String(params.slug)}`,
        }),
      ),
    ),
  ),
]

// Default test handlers: empty list + happy-path singletons.
export const projectDocsHandlers = [
  http.get('/api/v1/projects/:projectId/docs', () =>
    HttpResponse.json(
      paginatedFor<typeof listProjectDocs>(emptyPage<DocSummary>()),
    ),
  ),
  http.get('/api/v1/projects/:projectId/docs/search', () =>
    HttpResponse.json(
      successFor<typeof searchProjectDocs>([] as readonly DocSearchHit[]),
    ),
  ),
  http.get('/api/v1/projects/:projectId/docs/:slug', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getProjectDoc>(buildDoc({ slug: String(params.slug) })),
    ),
  ),
  http.get('/api/v1/projects/:projectId/docs/:slug/history', () =>
    HttpResponse.json(
      successFor<typeof getProjectDocHistory>([] as readonly DocVersion[]),
    ),
  ),
]
