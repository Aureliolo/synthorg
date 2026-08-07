import { renderHook, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import type { ActiveAgentSummary } from '@/api/types/agents'
import { judgedRoles, useOrgRoster } from '@/hooks/useOrgRoster'
import { pageEnvelope } from '@/mocks/handlers/helpers'
import { server } from '@/test-setup'

function agent(id: string, role: string): ActiveAgentSummary {
  return { id, name: `Agent ${id}`, role }
}

describe('useOrgRoster', () => {
  it('collects the roles the org staffs, across every page', async () => {
    server.use(
      http.get('/api/v1/agents/active', ({ request }) => {
        const cursor = new URL(request.url).searchParams.get('cursor')
        return HttpResponse.json(
          cursor === null
            ? pageEnvelope([agent('a1', 'Backend Developer')], { nextCursor: 'p2' })
            : pageEnvelope([agent('a2', 'Designer'), agent('a3', 'Designer')]),
        )
      }),
    )

    const { result } = renderHook(() => useOrgRoster())

    await waitFor(() => {
      expect(result.current.status).toBe('ready')
    })
    // Roles, not agents: two designers staff one role, and a role held only by
    // a later page must still count or a legitimate owner reads as unroutable.
    expect([...result.current.roles].sort()).toEqual(['Backend Developer', 'Designer'])
    expect(judgedRoles(result.current)).toBe(result.current.roles)
  })

  it('judges nothing while the roster is still loading', () => {
    const { result } = renderHook(() => useOrgRoster())

    expect(result.current.status).toBe('loading')
    expect(judgedRoles(result.current)).toBeUndefined()
  })

  it('judges nothing when the roster cannot be loaded', async () => {
    server.use(
      http.get('/api/v1/agents/active', () => new HttpResponse(null, { status: 500 })),
    )

    const { result } = renderHook(() => useOrgRoster())

    await waitFor(() => {
      expect(result.current.status).toBe('failed')
    })
    // An answer that never arrived must not flag every owner in the plan.
    expect(judgedRoles(result.current)).toBeUndefined()
  })

  it('reports an org with no agents as ready and empty', async () => {
    server.use(
      http.get('/api/v1/agents/active', () =>
        HttpResponse.json(pageEnvelope<ActiveAgentSummary>([])),
      ),
    )

    const { result } = renderHook(() => useOrgRoster())

    await waitFor(() => {
      expect(result.current.status).toBe('ready')
    })
    expect(judgedRoles(result.current)?.size).toBe(0)
  })
})
