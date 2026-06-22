import { http, HttpResponse } from 'msw'
import type {
  getProjectBrainEntry,
  getProjectBrainHistory,
  listProjectBrain,
  searchProjectBrain,
} from '@/api/endpoints/projectBrain'
import type {
  BrainEntry,
  BrainEntryVersion,
  BrainSearchHit,
  BrainSummary,
} from '@/api/types'
import { emptyPage, paginatedFor, successFor } from './helpers'

function buildEntry(overrides: Partial<BrainEntry> = {}): BrainEntry {
  return {
    entry_id: 'entry-default',
    revision: 1,
    project_id: 'proj-default',
    entry_kind: 'decision',
    title: 'Default decision',
    rationale: 'Default rationale.',
    status: 'accepted',
    author: 'agent-default',
    recorded_at: '2026-05-30T00:00:00Z',
    related_task_ids: [],
    related_entry_ids: [],
    supersedes_entry_id: null,
    tags: [],
    confidence: null,
    citations: [],
    payload: {
      entry_kind: 'decision',
      decision_outcome: 'append-only',
      alternatives: [],
    },
    ...overrides,
  }
}

// Default test handlers: empty list + happy-path singletons.
export const projectBrainHandlers = [
  http.get('/api/v1/projects/:projectId/brain', () =>
    HttpResponse.json(
      paginatedFor<typeof listProjectBrain>(emptyPage<BrainSummary>()),
    ),
  ),
  http.get('/api/v1/projects/:projectId/brain/search', () =>
    HttpResponse.json(
      successFor<typeof searchProjectBrain>([] as readonly BrainSearchHit[]),
    ),
  ),
  http.get('/api/v1/projects/:projectId/brain/:entryId', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getProjectBrainEntry>(
        buildEntry({ entry_id: String(params['entryId']) }),
      ),
    ),
  ),
  http.get('/api/v1/projects/:projectId/brain/:entryId/history', () =>
    HttpResponse.json(
      paginatedFor<typeof getProjectBrainHistory>(emptyPage<BrainEntryVersion>()),
    ),
  ),
]
