import { http, HttpResponse } from 'msw'
import { useMessagesStore, _resetRequestSeqs } from '@/stores/messages'
import { makeMessage, makeChannel } from '../helpers/factories'
import { apiError, paginatedFor } from '@/mocks/handlers'
import type { listMessages } from '@/api/endpoints/messages'
import { server } from '@/test-setup'
import type { Message } from '@/api/types/messages'
import type { WsEvent } from '@/api/types/websocket'

function paginated(
  data: Message[],
  meta: Partial<{
    total: number | null
    offset: number
    limit: number
    nextCursor: string | null
    hasMore: boolean
  }> = {},
) {
  // ``total`` is ``number | null`` under cursor pagination so the
  // repo-backed ``total === null`` path is exercisable from tests;
  // ``'total' in meta`` means the caller supplied an explicit value
  // (possibly ``null``), otherwise fall back to ``data.length``.
  const limit = meta.limit ?? 50
  // If the caller supplied a ``nextCursor`` without a ``hasMore``
  // (or vice versa), default the missing field to the consistent
  // counterpart so the helper cannot emit the impossible
  // ``has_more=true, next_cursor=null`` or ``next_cursor!=null,
  // has_more=false`` envelopes the backend's ``PaginationMeta``
  // consistency validator rejects.  The server-side tests would
  // otherwise pass even if the store silently dropped continuation
  // state.
  const nextCursor =
    meta.nextCursor !== undefined
      ? meta.nextCursor
      : meta.hasMore === true
        ? 'auto-continuation-cursor'
        : null
  const hasMore =
    meta.hasMore !== undefined ? meta.hasMore : nextCursor !== null
  return paginatedFor<typeof listMessages>({
    data,
    limit,
    nextCursor,
    hasMore,
    pagination: {
      limit,
      next_cursor: nextCursor,
      has_more: hasMore,
    },
  })
}

function resetStore() {
  _resetRequestSeqs()
  useMessagesStore.setState({
    channels: [],
    channelsLoading: false,
    channelsError: null,
    messages: [],
    nextCursor: null,
    hasMore: false,
    loading: false,
    loadingMore: false,
    error: null,
    unreadCounts: {},
    expandedThreads: new Set(),
    newMessageIds: new Set(),
  })
}

describe('messagesStore', () => {
  beforeEach(() => {
    resetStore()
  })

  describe('fetchChannels', () => {
    it('fetches and sets channels', async () => {
      const channels = [makeChannel('#engineering'), makeChannel('#product')]
      server.use(
        http.get('/api/v1/messages/channels', () =>
          HttpResponse.json({
            data: channels,
            error: null,
            error_detail: null,
            success: true,
            pagination: {
              limit: 50,
              next_cursor: null,
              has_more: false,
            },
          }),
        ),
      )

      await useMessagesStore.getState().fetchChannels()

      expect(useMessagesStore.getState().channels).toHaveLength(2)
      expect(useMessagesStore.getState().channelsLoading).toBe(false)
    })

    it('sets channelsError on failure', async () => {
      server.use(
        http.get('/api/v1/messages/channels', () =>
          HttpResponse.json(apiError('Network error')),
        ),
      )

      await useMessagesStore.getState().fetchChannels()

      expect(useMessagesStore.getState().channelsError).toBe('Network error')
      expect(useMessagesStore.getState().channelsLoading).toBe(false)
    })
  })

  describe('fetchMessages', () => {
    it('fetches and sets messages for a channel', async () => {
      const msgs = [makeMessage('1'), makeMessage('2')]
      server.use(
        http.get('/api/v1/messages', () =>
          HttpResponse.json(paginated(msgs)),
        ),
      )

      await useMessagesStore.getState().fetchMessages('#engineering')

      expect(useMessagesStore.getState().messages).toHaveLength(2)
      expect(useMessagesStore.getState().total).toBe(2)
      expect(useMessagesStore.getState().loading).toBe(false)
    })

    it('forwards the channel filter as a query param', async () => {
      let capturedChannel: string | null = null
      server.use(
        http.get('/api/v1/messages', ({ request }) => {
          capturedChannel = new URL(request.url).searchParams.get('channel')
          return HttpResponse.json(paginated([]))
        }),
      )

      await useMessagesStore.getState().fetchMessages('#engineering')

      expect(capturedChannel).toBe('#engineering')
    })

    it('discards stale responses on rapid channel switching', async () => {
      const slowMsgs = [makeMessage('slow')]
      const fastMsgs = [makeMessage('fast')]

      let release!: () => void
      const gate = new Promise<void>((resolve) => {
        release = resolve
      })
      server.use(
        http.get('/api/v1/messages', async ({ request }) => {
          const channel = new URL(request.url).searchParams.get('channel')
          if (channel === '#old-channel') {
            await gate
            return HttpResponse.json(paginated(slowMsgs, { total: 1 }))
          }
          return HttpResponse.json(paginated(fastMsgs, { total: 1 }))
        }),
      )

      const p1 = useMessagesStore.getState().fetchMessages('#old-channel')
      const p2 = useMessagesStore.getState().fetchMessages('#new-channel')
      await p2

      release()
      await p1

      expect(useMessagesStore.getState().messages[0]!.id).toBe('fast')
    })

    it('sets error on failure', async () => {
      server.use(
        http.get('/api/v1/messages', () =>
          HttpResponse.json(apiError('Server error')),
        ),
      )

      await useMessagesStore.getState().fetchMessages('#engineering')

      expect(useMessagesStore.getState().error).toBe('Server error')
    })
  })

  describe('fetchMoreMessages', () => {
    it('appends messages to existing list', async () => {
      useMessagesStore.setState({
        messages: [makeMessage('1')],
        nextCursor: 'cursor-page-2',
        hasMore: true,
      })
      server.use(
        http.get('/api/v1/messages', () =>
          HttpResponse.json(
            paginated([makeMessage('2'), makeMessage('3')], {
              nextCursor: null,
              hasMore: false,
            }),
          ),
        ),
      )

      await useMessagesStore.getState().fetchMoreMessages('#engineering')

      expect(useMessagesStore.getState().messages).toHaveLength(3)
      expect(useMessagesStore.getState().loadingMore).toBe(false)
    })

    it('skips if already loading more', async () => {
      useMessagesStore.setState({ loadingMore: true })
      let calls = 0
      server.use(
        http.get('/api/v1/messages', () => {
          calls += 1
          return HttpResponse.json(paginated([]))
        }),
      )

      await useMessagesStore.getState().fetchMoreMessages('#engineering')

      expect(calls).toBe(0)
    })

    it('sets error on failure', async () => {
      useMessagesStore.setState({
        messages: [makeMessage('1')],
        nextCursor: 'cursor-page-2',
        hasMore: true,
      })
      server.use(
        http.get('/api/v1/messages', () =>
          HttpResponse.json(apiError('Timeout')),
        ),
      )

      await useMessagesStore.getState().fetchMoreMessages('#engineering')

      expect(useMessagesStore.getState().error).toBe('Timeout')
      expect(useMessagesStore.getState().loadingMore).toBe(false)
    })

    it('discards stale response after channel switch', async () => {
      useMessagesStore.setState({
        messages: [makeMessage('1')],
        nextCursor: 'cursor-old-page-2',
        hasMore: true,
      })
      let release!: () => void
      const gate = new Promise<void>((resolve) => {
        release = resolve
      })
      server.use(
        http.get('/api/v1/messages', async ({ request }) => {
          const url = new URL(request.url)
          const channel = url.searchParams.get('channel')
          const cursor = url.searchParams.get('cursor')
          if (channel === '#old' && cursor) {
            await gate
            return HttpResponse.json(
              paginated([makeMessage('stale')], { total: 5, offset: 1 }),
            )
          }
          return HttpResponse.json(
            paginated([makeMessage('fresh')], { total: 1, offset: 0 }),
          )
        }),
      )

      const pMore = useMessagesStore.getState().fetchMoreMessages('#old')
      await useMessagesStore.getState().fetchMessages('#new')
      release()
      await pMore

      const { messages } = useMessagesStore.getState()
      expect(messages).toHaveLength(1)
      expect(messages[0]!.id).toBe('fresh')
    })
  })

  describe('handleWsEvent', () => {
    /**
     * Builds a WS event whose payload carries the message verbatim. The
     * helper accepts ``unknown`` so tests can pass either a typed
     * ``Message`` or a deliberately malformed object without juggling
     * casts at every call site.
     */
    const makeWsEvent = (message: unknown): WsEvent => ({
      event_type: 'message.sent',
      channel: 'messages',
      timestamp: new Date().toISOString(),
      payload: { message: message },
    })

    it('prepends message to active channel list', () => {
      useMessagesStore.setState({
        messages: [makeMessage('existing')],
      })
      const newMsg = makeMessage('new', { channel: '#engineering' })

      useMessagesStore
        .getState()
        .handleWsEvent(
          makeWsEvent(newMsg),
          '#engineering',
        )

      const { messages, total } = useMessagesStore.getState()
      expect(messages).toHaveLength(2)
      expect(messages[0]!.id).toBe('new')
      expect(total).toBe(2)
    })

    it('increments unread for inactive channel', () => {
      const newMsg = makeMessage('new', { channel: '#product' })

      useMessagesStore
        .getState()
        .handleWsEvent(
          makeWsEvent(newMsg),
          '#engineering',
        )

      expect(useMessagesStore.getState().unreadCounts['#product']).toBe(1)
      expect(useMessagesStore.getState().messages).toHaveLength(0)
    })

    it('skips malformed payloads', () => {
      const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      try {
        useMessagesStore
          .getState()
          .handleWsEvent(makeWsEvent({ bad: true }), '#engineering')
        expect(useMessagesStore.getState().messages).toHaveLength(0)
      } finally {
        consoleSpy.mockRestore()
      }
    })

    it('skips array payload.message', () => {
      const event: WsEvent = {
        event_type: 'message.sent',
        channel: 'messages',
        timestamp: new Date().toISOString(),
        payload: { message: [makeMessage('1')] },
      }

      useMessagesStore.getState().handleWsEvent(event, '#engineering')

      expect(useMessagesStore.getState().messages).toHaveLength(0)
    })

    it('deduplicates messages by id', () => {
      const msg = makeMessage('dup', { channel: '#eng' })
      useMessagesStore.setState({
        messages: [msg],
        total: 1,
      })

      useMessagesStore.getState().handleWsEvent(
        makeWsEvent(msg),
        '#eng',
      )

      expect(useMessagesStore.getState().messages).toHaveLength(1)
      expect(useMessagesStore.getState().total).toBe(1)
    })

    it('skips when payload has no message', () => {
      const event: WsEvent = {
        event_type: 'message.sent',
        channel: 'messages',
        timestamp: new Date().toISOString(),
        payload: {},
      }

      useMessagesStore.getState().handleWsEvent(event, '#engineering')

      expect(useMessagesStore.getState().messages).toHaveLength(0)
    })

    it('rejects messages with non-finite tokens_used in metadata', () => {
      const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      try {
        const msg = {
          ...makeMessage('inf-tokens'),
          metadata: {
            task_id: null,
            project_id: null,
            tokens_used: Number.POSITIVE_INFINITY,
            cost: null,
            extra: [] as [string, string][],
          },
        }
        useMessagesStore
          .getState()
          .handleWsEvent(makeWsEvent(msg), '#engineering')
        expect(useMessagesStore.getState().messages).toHaveLength(0)
      } finally {
        consoleSpy.mockRestore()
      }
    })

    it('rejects messages with malformed attachments', () => {
      const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      try {
        const msg = {
          ...makeMessage('bad-attach'),
          // ``null`` is not a valid structured part -- ``isPartsShape``
          // must reject the whole frame.
          attachments: [null] as unknown as Message['attachments'],
        }
        useMessagesStore
          .getState()
          .handleWsEvent(makeWsEvent(msg), '#engineering')
        expect(useMessagesStore.getState().messages).toHaveLength(0)
      } finally {
        consoleSpy.mockRestore()
      }
    })

    it('sanitizes nested part strings and metadata.extra tuples', () => {
      const RLO = String.fromCharCode(0x202e)
      const msg = {
        ...makeMessage('sanitize-nested', { channel: '#eng' }),
        // UriPart.uri is an untrusted wire string: ``sanitizePart``
        // must clamp it (strip the RLO bidi override) before storage.
        attachments: [
          { type: 'uri' as const, uri: `https://x.test/ref-1${RLO}` },
        ],
        metadata: {
          task_id: null,
          project_id: null,
          tokens_used: null,
          cost: null,
          extra: [[`key${RLO}`, `value${RLO}`]] as [string, string][],
        },
      }
      useMessagesStore.getState().handleWsEvent(makeWsEvent(msg), '#eng')
      const stored = useMessagesStore.getState().messages[0]
      const part = stored?.attachments[0]
      expect(part?.type).toBe('uri')
      expect(part && 'uri' in part ? part.uri : null).toBe(
        'https://x.test/ref-1',
      )
      expect(stored?.metadata.extra[0]).toEqual(['key', 'value'])
    })
  })

  describe('toggleThread', () => {
    it('adds task_id to expanded set', () => {
      useMessagesStore.getState().toggleThread('task-1')
      expect(
        useMessagesStore.getState().expandedThreads.has('task-1'),
      ).toBe(true)
    })

    it('removes task_id from expanded set on second toggle', () => {
      useMessagesStore.getState().toggleThread('task-1')
      useMessagesStore.getState().toggleThread('task-1')
      expect(
        useMessagesStore.getState().expandedThreads.has('task-1'),
      ).toBe(false)
    })
  })

  describe('resetUnread', () => {
    it('clears unread count for a channel', () => {
      useMessagesStore.setState({
        unreadCounts: { '#engineering': 5, '#product': 3 },
      })

      useMessagesStore.getState().resetUnread('#engineering')

      expect(
        useMessagesStore.getState().unreadCounts['#engineering'],
      ).toBeUndefined()
      expect(useMessagesStore.getState().unreadCounts['#product']).toBe(3)
    })

    it('is no-op for channels without unread counts', () => {
      const before = useMessagesStore.getState().unreadCounts

      useMessagesStore.getState().resetUnread('#nonexistent')

      expect(useMessagesStore.getState().unreadCounts).toBe(before)
    })
  })
})
