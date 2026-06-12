import { http, HttpResponse } from 'msw'
import type {
  ReadOnlyVersionHistoryClient,
  VersionSnapshot,
} from '@/api/endpoints/version-history'
import { emptyPage, paginatedFor, successFor } from './helpers'

// Type-only import: handlers must never value-import an endpoint
// module, or it pulls `@/api/client` into the global test-setup graph
// (evaluated before per-file `vi.mock` registration, which breaks
// mocks like client.test.ts's CSRF spy). The role-versions client is a
// factory, so bind the handler types to the read-only client's methods.
type RoleVersionsClient = ReadOnlyVersionHistoryClient<Record<string, unknown>>
type RoleSnapshot = VersionSnapshot<Record<string, unknown>>

function buildRoleSnapshot(role: string, version: number): RoleSnapshot {
  return {
    entity_id: role,
    version,
    content_hash: 'sha256:0',
    saved_at: '2026-04-19T00:00:00Z',
    saved_by: 'user-1',
    snapshot: {},
  }
}

export const rolesHandlers = [
  http.get('/api/v1/roles/:role/versions', () =>
    HttpResponse.json(
      paginatedFor<RoleVersionsClient['list']>(emptyPage<RoleSnapshot>()),
    ),
  ),
  http.get('/api/v1/roles/:role/versions/:version', ({ params }) =>
    HttpResponse.json(
      successFor<RoleVersionsClient['get']>(
        buildRoleSnapshot(String(params['role']), Number(params['version'])),
      ),
    ),
  ),
]
