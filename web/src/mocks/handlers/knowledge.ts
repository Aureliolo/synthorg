import { http, HttpResponse } from 'msw'
import type {
  getProjectKnowledgeSource,
  listGlobalKnowledgeSources,
  listProjectKnowledgeSources,
  searchProjectKnowledge,
} from '@/api/endpoints/knowledge'
import type { KnowledgeHit, KnowledgeSource } from '@/api/types'
import { emptyPage, paginatedFor, successFor } from './helpers'

function buildSource(overrides: Partial<KnowledgeSource> = {}): KnowledgeSource {
  return {
    source_id: 'src-default',
    source_type: 'web',
    project_id: 'proj-default',
    uri: 'https://docs.test/default',
    title: 'Default source',
    content_hash: '0'.repeat(64),
    status: 'indexed',
    chunk_count: 0,
    is_global: false,
    created_at: '2026-05-20T00:00:00Z',
    updated_at: '2026-05-20T00:00:00Z',
    last_indexed_at: '2026-05-20T00:00:00Z',
    last_error: null,
    ...overrides,
  }
}

// Default test handlers: empty list + happy-path singletons.
export const knowledgeHandlers = [
  http.get('/api/v1/projects/:projectId/knowledge', () =>
    HttpResponse.json(
      paginatedFor<typeof listProjectKnowledgeSources>(
        emptyPage<KnowledgeSource>(),
      ),
    ),
  ),
  http.get('/api/v1/projects/:projectId/knowledge/search', () =>
    HttpResponse.json(
      successFor<typeof searchProjectKnowledge>([] as readonly KnowledgeHit[]),
    ),
  ),
  http.get('/api/v1/projects/:projectId/knowledge/:sourceId', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getProjectKnowledgeSource>(
        buildSource({ source_id: String(params['sourceId']) }),
      ),
    ),
  ),
  http.get('/api/v1/knowledge', () =>
    HttpResponse.json(
      paginatedFor<typeof listGlobalKnowledgeSources>(
        emptyPage<KnowledgeSource>(),
      ),
    ),
  ),
]
