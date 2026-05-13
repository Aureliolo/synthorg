import { http, HttpResponse } from 'msw'
import type {
  getTunnelStatus,
  startTunnel,
} from '@/api/endpoints/tunnel'
import { successFor, voidSuccess } from './helpers'

// ── Storybook-facing named export (stateful tunnel url for story demos). ──
const tunnelState: { url: string | null } = { url: null }

export const tunnelHandlers = [
  http.get('/api/v1/integrations/tunnel/status', () =>
    HttpResponse.json(
      successFor<typeof getTunnelStatus>({
        public_url: tunnelState.url,
        has_auth_token: true,
      }),
    ),
  ),
  http.post('/api/v1/integrations/tunnel/start', () => {
    tunnelState.url = 'https://mock-tunnel.ngrok.io'
    return HttpResponse.json(
      successFor<typeof startTunnel>({ public_url: tunnelState.url }),
    )
  }),
  http.post('/api/v1/integrations/tunnel/stop', () => {
    tunnelState.url = null
    return HttpResponse.json(voidSuccess())
  }),
]

// ── Default test handlers (tunnel inactive). ──
export const tunnelDefaultHandlers = [
  http.get('/api/v1/integrations/tunnel/status', () =>
    HttpResponse.json(
      successFor<typeof getTunnelStatus>({
        public_url: null,
        has_auth_token: false,
      }),
    ),
  ),
  http.post('/api/v1/integrations/tunnel/start', () =>
    HttpResponse.json(
      successFor<typeof startTunnel>({ public_url: 'https://mock-tunnel.ngrok.io' }),
    ),
  ),
  http.post('/api/v1/integrations/tunnel/stop', () => HttpResponse.json(voidSuccess())),
]
