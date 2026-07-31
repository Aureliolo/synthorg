import { http, HttpResponse } from 'msw'
import type { probeEmbedder } from '@/api/endpoints/memory'
import { successFor, voidSuccess } from './helpers'

export const memoryHandlers = [
  http.delete('/api/v1/admin/memory/agents/:agentId/memories/:memoryId', () =>
    HttpResponse.json(voidSuccess()),
  ),
  // Embedder width probe. The happy path is an indexable width, so a test
  // that cares about the over-ceiling case overrides this deliberately.
  http.post('/api/v1/admin/memory/embedder/probe', () =>
    HttpResponse.json(
      successFor<typeof probeEmbedder>({
        dims: 1024,
        index_support: 'indexed',
        vector_ceiling: 2000,
        halfvec_ceiling: 4000,
      }),
    ),
  ),
]
