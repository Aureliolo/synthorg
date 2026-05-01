import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, beforeEach } from 'vitest'
import { http, HttpResponse } from 'msw'
import { useCommunicationEdges } from '@/hooks/useCommunicationEdges'
import { apiError, paginatedFor } from '@/mocks/handlers'
import type { listMessages } from '@/api/endpoints/messages'
import { server } from '@/test-setup'
import type { Message } from '@/api/types/messages'

type MessageSeed = { sender: string; to: string }

function paginated(
  data: MessageSeed[],
  meta: {
    limit: number
    offset?: number
    nextCursor?: string | null
    hasMore?: boolean
  },
) {
  // Seeds are minimal sender/to records; the hook only reads those two
  // fields. ``paginatedFor`` demands the full Message shape, so we
  // build a minimum-viable Message per seed.
  const messages: Message[] = data.map((seed, idx) => ({
    // Make ids globally unique across paginated pages so the
    // hook's de-dup logic sees distinct messages instead of
    // collapsing them -- ``idx`` alone resets per page and would
    // produce collisions (page 0 ``msg-0`` == page 1 ``msg-0``).
    id: `msg-${(meta.offset ?? 0) + idx}`,
    timestamp: '2026-04-21T00:00:00Z',
    sender: seed.sender,
    to: seed.to,
    type: 'task_update',
    priority: 'normal',
    channel: 'direct',
    content: '',
    attachments: [],
    metadata: {
      task_id: null,
      project_id: null,
      tokens_used: null,
      cost: null,
      extra: [],
    },
  }))
  const nextCursor = meta.nextCursor ?? null
  const hasMore = meta.hasMore ?? false
  return paginatedFor<typeof listMessages>({
    data: messages,
    limit: meta.limit,
    nextCursor,
    hasMore,
    pagination: {
      limit: meta.limit,
      next_cursor: nextCursor,
      has_more: hasMore,
    },
  })
}

describe('useCommunicationEdges', () => {
  let messagesCalls: Array<{
    cursor: string | null
    limit: string | null
  }> = []

  beforeEach(() => {
    messagesCalls = []
  })

  it('returns empty links when disabled', () => {
    const { result } = renderHook(() => useCommunicationEdges(false))
    expect(result.current.links).toEqual([])
    expect(result.current.loading).toBe(false)
    expect(messagesCalls).toHaveLength(0)
  })

  it('fetches and aggregates messages', async () => {
    server.use(
      http.get('/api/v1/messages', () =>
        HttpResponse.json(
          paginated(
            [
              { sender: 'alice', to: 'bob' },
              { sender: 'bob', to: 'alice' },
              { sender: 'alice', to: 'carol' },
            ],
            { offset: 0, limit: 100 },
          ),
        ),
      ),
    )

    const { result } = renderHook(() => useCommunicationEdges(true))

    expect(result.current.loading).toBe(true)

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    expect(result.current.links).toHaveLength(2)
    const abLink = result.current.links.find(
      (l) => l.source === 'alice' && l.target === 'bob',
    )
    expect(abLink).toBeDefined()
    expect(abLink!.volume).toBe(2)
  })

  it('handles pagination across multiple pages', async () => {
    const page1Data = Array.from({ length: 100 }, (_, i) => ({
      sender: `agent-${i}`,
      to: `agent-${i + 1}`,
    }))
    const page2Data = [{ sender: 'carol', to: 'dave' }]
    const PAGE_2_CURSOR = 'cursor-page-2'
    server.use(
      http.get('/api/v1/messages', ({ request }) => {
        const params = new URL(request.url).searchParams
        messagesCalls.push({
          cursor: params.get('cursor'),
          limit: params.get('limit'),
        })
        if (params.get('cursor') === PAGE_2_CURSOR) {
          return HttpResponse.json(
            paginated(page2Data, {
              limit: 100,
              nextCursor: null,
              hasMore: false,
            }),
          )
        }
        return HttpResponse.json(
          paginated(page1Data, {
            limit: 100,
            nextCursor: PAGE_2_CURSOR,
            hasMore: true,
          }),
        )
      }),
    )

    const { result } = renderHook(() => useCommunicationEdges(true))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    expect(result.current.links.length).toBeGreaterThan(0)
    expect(messagesCalls).toHaveLength(2)
    expect(messagesCalls[0]).toEqual({ cursor: null, limit: '100' })
    expect(messagesCalls[1]).toEqual({ cursor: PAGE_2_CURSOR, limit: '100' })
  })

  it('sets error on API failure', async () => {
    server.use(
      http.get('/api/v1/messages', () =>
        HttpResponse.json(apiError('Network error')),
      ),
    )

    const { result } = renderHook(() => useCommunicationEdges(true))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    expect(result.current.error).toBe('Network error')
    expect(result.current.links).toEqual([])
  })

  it('returns empty links when no messages exist', async () => {
    server.use(
      http.get('/api/v1/messages', () =>
        HttpResponse.json(paginated([], { offset: 0, limit: 100 })),
      ),
    )

    const { result } = renderHook(() => useCommunicationEdges(true))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    expect(result.current.links).toEqual([])
    expect(result.current.error).toBeNull()
  })
})
