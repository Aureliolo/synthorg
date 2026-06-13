import { http, HttpResponse } from 'msw'
import type {
  grantOrgRole,
  listUsers,
  UserResponse,
} from '@/api/endpoints/users'
import type { OrgRole } from '@/api/types/enums'
import { emptyPage, paginatedFor, successFor, voidSuccess } from './helpers'

function buildUser(overrides: Partial<UserResponse> = {}): UserResponse {
  return {
    id: 'user-default',
    username: 'default',
    role: 'ceo',
    must_change_password: false,
    org_roles: [],
    scoped_departments: [],
    created_at: '2026-04-19T00:00:00Z',
    updated_at: '2026-04-19T00:00:00Z',
    ...overrides,
  }
}

export const usersHandlers = [
  http.get('/api/v1/users', () =>
    HttpResponse.json(paginatedFor<typeof listUsers>(emptyPage<UserResponse>())),
  ),
  http.post('/api/v1/users/:id/org-roles', async ({ params, request }) => {
    const body = (await request.json()) as {
      role?: OrgRole
      scoped_departments?: string[]
    }
    return HttpResponse.json(
      successFor<typeof grantOrgRole>(
        buildUser({
          id: String(params['id']),
          org_roles: body.role ? [body.role] : [],
          scoped_departments: body.scoped_departments ?? [],
        }),
      ),
    )
  }),
  http.delete('/api/v1/users/:id/org-roles/:role', () =>
    HttpResponse.json(voidSuccess()),
  ),
]
