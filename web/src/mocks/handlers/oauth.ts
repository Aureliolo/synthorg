import { http, HttpResponse } from 'msw'
import type { getOauthStatus, initiateOauth } from '@/api/endpoints/oauth'
import { successFor } from './helpers'

// ── Storybook-facing named export (typed to the endpoint return types
// so stories cannot drift from the live API shape). ──
export const oauthHandlers = [
  http.post('/api/v1/oauth/initiate', () =>
    HttpResponse.json(
      successFor<typeof initiateOauth>({
        authorization_url: 'https://example.com/oauth/authorize?state=abc',
        state_token: 'mock-state-token',
      }),
    ),
  ),
  http.get('/api/v1/oauth/status/:connectionName', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getOauthStatus>({
        connection_name: String(params['connectionName']),
        has_token: true,
        token_expires_at: '2026-05-11T12:00:00Z',
      }),
    ),
  ),
]

// ── Default test handlers (same URLs, typed-for-the-endpoint). ──
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
