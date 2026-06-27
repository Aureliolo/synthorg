import { http, HttpResponse } from 'msw'
import type { getOauthStatus, initiateOauth } from '@/api/endpoints/oauth'
import { successFor } from './helpers'

// ── Default test handlers (typed to the endpoint return types so the
// mocks cannot drift from the live API shape). ──
export const oauthDefaultHandlers = [
  http.post('/api/v1/oauth/initiate', () =>
    HttpResponse.json(
      successFor<typeof initiateOauth>({
        authorization_url: 'https://example.com/oauth/authorize',
        state_token: 'mock-state-token',
      }),
    ),
  ),
  http.get('/api/v1/oauth/status/:connectionName', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getOauthStatus>({
        connection_name: String(params['connectionName']),
        has_token: false,
        token_expires_at: null,
      }),
    ),
  ),
]
