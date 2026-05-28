import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { createRoleVersionsClient } from '@/api/endpoints/version-history'
import { server } from '@/test-setup'

describe('createRoleVersionsClient', () => {
  it('lists versions at the role-scoped, URL-encoded path', async () => {
    let captured = ''
    server.use(
      http.get('/api/v1/roles/:role/versions', ({ request }) => {
        captured = new URL(request.url).pathname
        return HttpResponse.json({
          data: [],
          error: null,
          error_detail: null,
          pagination: { limit: 25, next_cursor: null, has_more: false },
          success: true,
        })
      }),
    )

    const client = createRoleVersionsClient('Lead Developer')
    const page = await client.list({ limit: 25 })

    expect(captured).toBe('/api/v1/roles/Lead%20Developer/versions')
    expect(page.data).toEqual([])
  })

  it('fetches a single version at the role-scoped path', async () => {
    let captured = ''
    server.use(
      http.get('/api/v1/roles/:role/versions/:version', ({ request }) => {
        captured = new URL(request.url).pathname
        return HttpResponse.json({
          data: {
            entity_id: 'role-x',
            version: 3,
            content_hash: 'h',
            saved_at: '2026-04-19T00:00:00Z',
            saved_by: 'user-1',
            snapshot: {},
          },
          error: null,
          error_detail: null,
          success: true,
        })
      }),
    )

    const client = createRoleVersionsClient('QA Engineer')
    const snapshot = await client.get(3)

    expect(captured).toBe('/api/v1/roles/QA%20Engineer/versions/3')
    expect(snapshot.version).toBe(3)
  })
})
