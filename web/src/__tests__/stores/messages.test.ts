import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useMessageStore } from '@/stores/messages'
import type { WsEvent } from '@/api/types'

vi.mock('@/api/endpoints/messages', () => ({
  listMessages: vi.fn(),
  listChannels: vi.fn(),
}))

describe('useMessageStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('initializes with empty state', () => {
    const store = useMessageStore()
    expect(store.messages).toEqual([])
    expect(store.channels).toEqual([])
    expect(store.activeChannel).toBeNull()
  })

  it('handles message.sent WS event', () => {
    const store = useMessageStore()
    const event: WsEvent = {
      event_type: 'message.sent',
      channel: 'messages',
      timestamp: '2026-03-12T10:00:00Z',
      payload: {
        id: 'msg-1',
        channel: 'general',
        sender: 'alice',
        content: 'Hello world',
        timestamp: '2026-03-12T10:00:00Z',
        metadata: {},
      },
    }
    store.handleWsEvent(event)
    expect(store.messages).toHaveLength(1)
    // total is only updated from REST API, not WS events
    expect(store.total).toBe(0)
  })

  it('does not increment total for messages filtered by activeChannel', () => {
    const store = useMessageStore()
    store.setActiveChannel('engineering')
    const event: WsEvent = {
      event_type: 'message.sent',
      channel: 'messages',
      timestamp: '2026-03-12T10:00:00Z',
      payload: {
        id: 'msg-1',
        channel: 'general', // does not match activeChannel 'engineering'
        sender: 'alice',
        content: 'Hello world',
        timestamp: '2026-03-12T10:00:00Z',
        metadata: {},
      },
    }
    store.handleWsEvent(event)
    expect(store.messages).toHaveLength(0)
    expect(store.total).toBe(0) // not incremented for filtered-out messages
  })

  it('ignores message.sent with malformed payload', () => {
    const store = useMessageStore()
    const event: WsEvent = {
      event_type: 'message.sent',
      channel: 'messages',
      timestamp: '2026-03-12T10:00:00Z',
      payload: { id: 'msg-1' }, // missing channel, sender, content, timestamp
    }
    store.handleWsEvent(event)
    expect(store.messages).toHaveLength(0)
  })

  it('setActiveChannel updates state', () => {
    const store = useMessageStore()
    store.setActiveChannel('general')
    expect(store.activeChannel).toBe('general')
  })
})
