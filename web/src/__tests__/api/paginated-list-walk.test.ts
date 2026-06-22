import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { listActiveAgents } from '@/api/endpoints/agents'
import { listCustomRules } from '@/api/endpoints/custom-rules'
import { buildCustomRule, pageEnvelope } from '@/mocks/handlers'
import { server } from '@/test-setup'

describe('listActiveAgents (paginated envelope)', () => {
  it('walks every cursor page rather than dropping rows past the first page', async () => {
    const cursors: (string | null)[] = []
    server.use(
      http.get('/api/v1/agents/active', ({ request }) => {
        const cursor = new URL(request.url).searchParams.get('cursor')
        cursors.push(cursor)
        if (cursor === null) {
          return HttpResponse.json(
            pageEnvelope([{ id: 'a-1', name: 'Dana', role: 'CEO' }], { nextCursor: 'c2' }),
          )
        }
        return HttpResponse.json(pageEnvelope([{ id: 'a-2', name: 'Casey', role: 'CFO' }]))
      }),
    )

    const agents = await listActiveAgents()

    expect(agents.map((a) => a.id)).toEqual(['a-1', 'a-2'])
    expect(cursors).toEqual([null, 'c2'])
  })
})

describe('listCustomRules (paginated envelope)', () => {
  it('walks every cursor page and returns the full rule set', async () => {
    server.use(
      http.get('/api/v1/meta/custom-rules', ({ request }) => {
        const cursor = new URL(request.url).searchParams.get('cursor')
        if (cursor === null) {
          return HttpResponse.json(
            pageEnvelope([buildCustomRule({ id: 'r-1', name: 'one' })], { nextCursor: 'next' }),
          )
        }
        return HttpResponse.json(pageEnvelope([buildCustomRule({ id: 'r-2', name: 'two' })]))
      }),
    )

    const rules = await listCustomRules()

    expect(rules.map((r) => r.id)).toEqual(['r-1', 'r-2'])
  })
})
