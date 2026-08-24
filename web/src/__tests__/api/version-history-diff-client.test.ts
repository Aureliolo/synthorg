import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { diffAgentIdentityVersions } from '@/api/endpoints/agents'
import { diffWorkflowVersions } from '@/api/endpoints/workflows'
import type { AgentIdentityDiff } from '@/api/types/agents'
import type { WorkflowDiff } from '@/api/types/workflows'
import { server } from '@/test-setup'

/**
 * Each diff domain uses a distinct URL and response shape: agent identity
 * serves ``AgentIdentityDiff`` (``field_changes``) at
 * ``/<base>/versions/diff``; workflows serve ``WorkflowDiff``
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
        // Typing the fixture against the generated DTO pins the mock so a
        // backend schema rename (e.g. ``field_changes`` -> ``field_diffs``)
        // fails this test at type-check instead of passing against stale data.
        const data: AgentIdentityDiff = {
          agent_id: String(p['id']),
          from_version: 1,
          to_version: 3,
          field_changes: [
            {
              field_path: 'authority.budget_limit',
              change_type: 'modified',
              old_value: 'low',
              new_value: 'high',
            },
          ],
          summary: '1 field changed',
        }
        return HttpResponse.json({
          data,
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
          path: 'authority.budget_limit',
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
        const data: WorkflowDiff = {
          definition_id: String(p['id']),
          from_version: 2,
          to_version: 4,
          node_changes: [
            {
              node_id: 'n1', node_label: 'Draft brief',
              change_type: 'added', old_value: null, new_value: { type: 'task' },
            },
          ],
          edge_changes: [
            {
              edge_id: 'e1', edge_label: null,
              source_label: 'Draft brief', target_label: 'Review',
              change_type: 'removed', old_value: { from: 'a' }, new_value: null,
            },
          ],
          metadata_changes: [{ field: 'name', old_value: 'old', new_value: 'new' }],
          summary: '3 changes',
        }
        return HttpResponse.json({
          data,
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
    // Named by label, never by id: `node:n1` in the drawer describes a change
    // to something the operator has never seen. The unlabelled edge falls back
    // to the two steps it joins, which is how it is known.
    expect(diff.entries).toEqual([
      { path: 'node:Draft brief', before: null, after: { type: 'task' } },
      { path: 'edge:Draft brief to Review', before: { from: 'a' }, after: null },
      { path: 'metadata:name', before: 'old', after: 'new' },
    ])
  })

  it('workflow: names an unlabelled node and edge in its own words, not by id', async () => {
    server.use(
      http.get('/api/v1/workflows/:id/diff', ({ params: p }) => {
        const data: WorkflowDiff = {
          definition_id: String(p['id']),
          from_version: 1,
          to_version: 2,
          node_changes: [
            {
              node_id: 'n9', node_label: null,
              change_type: 'removed', old_value: { type: 'task' }, new_value: null,
            },
          ],
          edge_changes: [
            {
              edge_id: 'e9', edge_label: null,
              source_label: null, target_label: null,
              change_type: 'added', old_value: null, new_value: { to: 'z' },
            },
          ],
          metadata_changes: [],
          summary: '2 changes',
        }
        return HttpResponse.json({
          data,
          error: null,
          error_detail: null,
          success: true,
        })
      }),
    )

    const diff = await diffWorkflowVersions('wf-1', 1, 2)

    // Nothing named either one, so the surface says so. It never falls back to
    // the key, which is the outcome the label fields exist to prevent.
    expect(diff.entries).toEqual([
      { path: 'node:an unnamed step', before: { type: 'task' }, after: null },
      { path: 'edge:an unnamed connection', before: null, after: { to: 'z' } },
    ])
  })
})
