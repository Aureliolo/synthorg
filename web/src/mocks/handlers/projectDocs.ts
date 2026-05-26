import { http, HttpResponse } from 'msw'
import type {
  getProjectDoc,
  getProjectDocHistory,
  listProjectDocs,
  searchProjectDocs,
} from '@/api/endpoints/projectDocs'
import type {
  DocSearchHit,
  DocSummary,
  DocVersion,
  LivingDocument,
} from '@/api/types'
import {
  emptyPage,
  paginatedFor,
  successFor,
} from './helpers'

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
