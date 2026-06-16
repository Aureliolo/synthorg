import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { diffAgentIdentityVersions } from '@/api/endpoints/agents'
import { diffWorkflowVersions } from '@/api/endpoints/workflows'
import { server } from '@/test-setup'

/**
 * Regression: the version-history diff client previously called the
 * generic ``/<base>/versions/diff`` URL for every domain and decoded the
 * response as ``{ entries: [{ before, after }] }``. Both assumptions were
 * wrong: agent identity serves ``AgentIdentityDiff`` (``field_changes``)
 * at ``/<base>/versions/diff`` while workflows serve ``WorkflowDiff``
 * (``node_changes`` / ``edge_changes`` / ``metadata_changes``) at
 * ``/<base>/diff``. These pin both per-domain URLs and the normalisation
 * into the shared ``VersionDiffResponse`` the drawer renders.
 */
describe('version-history per-domain diff', () => {
  it('agent identity: GETs /versions/diff and flattens field_changes', async () => {
    let url = ''
    let fromParam = ''
    let toParam = ''
    server.use(
      http.get('/api/v1/agents/:id/versions/diff', ({ request, params: p }) => {
        const parsed = new URL(request.url)
        url = parsed.pathname
        fromParam = parsed.searchParams.get('from_version') ?? ''
        toParam = parsed.searchParams.get('to_version') ?? ''
        return HttpResponse.json({
          data: {
            agent_id: String(p['id']),
            from_version: 1,
            to_version: 3,
            field_changes: [
              {
                field_path: 'personality.risk_tolerance',
                change_type: 'modified',
                old_value: 'low',
                new_value: 'high',
              },
            ],
            summary: '1 field changed',
          },
          error: null,
          error_detail: null,
          success: true,
        })
      }),
    )

    const diff = await diffAgentIdentityVersions('agent-7', 1, 3)

    expect(url).toBe('/api/v1/agents/agent-7/versions/diff')
    expect(fromParam).toBe('1')
    expect(toParam).toBe('3')
    expect(diff).toEqual({
      from_version: 1,
      to_version: 3,
      entries: [
        {
          path: 'personality.risk_tolerance',
          before: 'low',
          after: 'high',
        },
      ],
    })
  })

  it('workflow: GETs /diff and flattens node/edge/metadata changes', async () => {
    let url = ''
    server.use(
      http.get('/api/v1/workflows/:id/diff', ({ request, params: p }) => {
        url = new URL(request.url).pathname
        return HttpResponse.json({
          data: {
            definition_id: String(p['id']),
            from_version: 2,
            to_version: 4,
            node_changes: [
              { node_id: 'n1', change_type: 'added', old_value: null, new_value: { type: 'task' } },
            ],
            edge_changes: [
              { edge_id: 'e1', change_type: 'removed', old_value: { from: 'a' }, new_value: null },
            ],
            metadata_changes: [{ field: 'name', old_value: 'old', new_value: 'new' }],
            summary: '3 changes',
          },
          error: null,
          error_detail: null,
          success: true,
        })
      }),
    )

    const diff = await diffWorkflowVersions('wf-1', 2, 4)

    expect(url).toBe('/api/v1/workflows/wf-1/diff')
    expect(diff.from_version).toBe(2)
    expect(diff.to_version).toBe(4)
    expect(diff.entries).toEqual([
      { path: 'node:n1', before: null, after: { type: 'task' } },
      { path: 'edge:e1', before: { from: 'a' }, after: null },
      { path: 'metadata:name', before: 'old', after: 'new' },
    ])
  })
})
