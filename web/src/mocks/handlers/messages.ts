import { http, HttpResponse } from 'msw'
import type { listChannels, listMessages } from '@/api/endpoints/messages'
import type { Channel, Message } from '@/api/types/messages'
import { emptyPage, paginatedFor, voidSuccess } from './helpers'

export const messagesHandlers = [
  http.get('/api/v1/messages', () =>
    HttpResponse.json(paginatedFor<typeof listMessages>(emptyPage<Message>())),
  ),
  http.get('/api/v1/messages/channels', () =>
    HttpResponse.json(paginatedFor<typeof listChannels>(emptyPage<Channel>())),
  ),
  http.delete('/api/v1/messages/:id', () =>
    HttpResponse.json(voidSuccess()),
  ),
]
