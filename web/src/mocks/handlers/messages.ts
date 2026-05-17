import { http, HttpResponse } from 'msw'
import type { listChannels, listMessages } from '@/api/endpoints/messages'
import type { Channel, Message } from '@/api/types/messages'
import { emptyPage, paginatedFor } from './helpers'

export function buildMessage(overrides: Partial<Message> = {}): Message {
  return {
    id: 'msg-default',
    timestamp: '2026-04-19T00:00:00Z',
    sender: 'agent-default',
    to: 'general',
    type: 'announcement',
    priority: 'normal',
    channel: 'general',
    text: 'default',
    parts: [{ type: 'text', text: 'default' }],
    attachments: [],
    metadata: {
      task_id: null,
      project_id: null,
      tokens_used: null,
      cost: null,
      extra: [],
    },
    ...overrides,
  }
}

export function buildChannel(overrides: Partial<Channel> = {}): Channel {
  return {
    name: 'general',
    type: 'topic',
    subscribers: [],
    ...overrides,
  }
}

export const messagesHandlers = [
  http.get('/api/v1/messages', () =>
    HttpResponse.json(paginatedFor<typeof listMessages>(emptyPage<Message>())),
  ),
  http.get('/api/v1/messages/channels', () =>
    HttpResponse.json(paginatedFor<typeof listChannels>(emptyPage<Channel>())),
  ),
]
