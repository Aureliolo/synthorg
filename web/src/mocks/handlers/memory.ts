import { http, HttpResponse } from 'msw'
import { voidSuccess } from './helpers'

export const memoryHandlers = [
  http.delete('/api/v1/admin/memory/agents/:agentId/memories/:memoryId', () =>
    HttpResponse.json(voidSuccess()),
  ),
]
