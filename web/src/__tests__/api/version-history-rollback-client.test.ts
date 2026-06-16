import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { rollbackAgentIdentity } from '@/api/endpoints/agents'
import { createVersionHistoryClient } from '@/api/endpoints/version-history'
import { getWorkflow, rollbackWorkflow } from '@/api/endpoints/workflows'
import { server } from '@/test-setup'

/**
 * Regression: the version-history rollback client previously sent
 * ``{ to_version }`` to ``/<base>/versions/rollback`` for every domain.
 * The two rollback-capable domains diverge: agent identity posts
 * ``{ target_version, reason }`` to ``/<base>/versions/rollback`` and
 * workflows post ``{ target_version, expected_revision }`` to
 * ``/<base>/rollback``. These pin both contracts via the per-domain
 * rollback descriptor the dialog drives.
 */
describe('version-history per-domain rollback', () => {
  it('agent identity: posts target_version + reason to /versions/rollback', async () => {
    let url = ''
    let body: Record<string, unknown> = {}
    server.use(
      http.post('/api/v1/agents/:id/versions/rollback', async ({ request }) => {
        url = new URL(request.url).pathname
        body = (await request.json()) as Record<string, unknown>
        return HttpResponse.json({
          data: { id: 'agent-7' },
          error: null,
          error_detail: null,
          success: true,
        })
      }),
    )

    const client = createVersionHistoryClient<Record<string, unknown>>(
      '/agents/agent-7',
      (input) =>
        rollbackAgentIdentity('agent-7', {
          target_version: input.targetVersion,
          reason: input.reason,
        }),
    )
    await client.rollback({ targetVersion: 4, reason: 'revert bad edit' })

    expect(url).toBe('/api/v1/agents/agent-7/versions/rollback')
    expect(body).toEqual({ target_version: 4, reason: 'revert bad edit' })
  })

  it('workflow: posts target_version + live expected_revision to /rollback', async () => {
    let url = ''
    let body: Record<string, unknown> = {}
    server.use(
      http.get('/api/v1/workflows/:id', ({ params }) =>
        HttpResponse.json({
          data: { id: String(params['id']), revision: 9 },
          error: null,
          error_detail: null,
          success: true,
        }),
      ),
      http.post('/api/v1/workflows/:id/rollback', async ({ request }) => {
        url = new URL(request.url).pathname
        body = (await request.json()) as Record<string, unknown>
        return HttpResponse.json({
          data: { id: 'wf-1', revision: 10 },
          error: null,
          error_detail: null,
          success: true,
        })
      }),
    )

    const client = createVersionHistoryClient<Record<string, unknown>>(
      '/workflows/wf-1',
      async (input) => {
        const defn = await getWorkflow('wf-1')
        return rollbackWorkflow('wf-1', {
          target_version: input.targetVersion,
          expected_revision: defn.revision,
        })
      },
    )
    await client.rollback({ targetVersion: 2, reason: 'ignored by workflow' })

    expect(url).toBe('/api/v1/workflows/wf-1/rollback')
    expect(body).toEqual({ target_version: 2, expected_revision: 9 })
  })
})
