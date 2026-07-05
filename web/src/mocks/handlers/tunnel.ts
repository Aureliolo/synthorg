import { http, HttpResponse } from 'msw'
import type {
  beginTunnelDeviceLogin,
  getTunnelStatus,
  startTunnel,
} from '@/api/endpoints/tunnel'
import type { TunnelProviderStatus, TunnelSnapshot } from '@/api/types/integrations'
import { successFor, voidSuccess } from './helpers'

export const mockTunnelProviders: readonly TunnelProviderStatus[] = [
  {
    provider_id: 'cloudflare',
    display_name: 'Cloudflare quick tunnel',
    credential_kind: 'none',
    available: true,
    detail: null,
    credential_configured: true,
  },
  {
    provider_id: 'ngrok',
    display_name: 'ngrok',
    credential_kind: 'token',
    available: true,
    detail: null,
    credential_configured: false,
  },
  {
    provider_id: 'devtunnels',
    display_name: 'Dev Tunnels',
    credential_kind: 'device_login',
    available: false,
    detail: 'The devtunnel CLI is not installed.',
    credential_configured: false,
  },
]

function snapshot(overrides?: Partial<TunnelSnapshot>): TunnelSnapshot {
  return {
    public_url: null,
    selected_provider: 'cloudflare',
    active_provider: null,
    providers: mockTunnelProviders,
    ...overrides,
  }
}

// ── Storybook-facing named export (stateful tunnel url for story demos). ──
const tunnelState: { url: string | null } = { url: null }

export const tunnelHandlers = [
  http.get('/api/v1/integrations/tunnel/status', () =>
    HttpResponse.json(
      successFor<typeof getTunnelStatus>(
        snapshot({
          public_url: tunnelState.url,
          active_provider: tunnelState.url ? 'cloudflare' : null,
        }),
      ),
    ),
  ),
  http.post('/api/v1/integrations/tunnel/start', () => {
    tunnelState.url = 'https://mock-tunnel.trycloudflare.com'
    return HttpResponse.json(
      successFor<typeof startTunnel>({
        public_url: tunnelState.url,
        provider: 'cloudflare',
      }),
    )
  }),
  http.post('/api/v1/integrations/tunnel/stop', () => {
    tunnelState.url = null
    return HttpResponse.json(voidSuccess())
  }),
  http.put('/api/v1/integrations/tunnel/credential', () => HttpResponse.json(voidSuccess())),
  http.delete('/api/v1/integrations/tunnel/credential/:provider', () =>
    HttpResponse.json(voidSuccess()),
  ),
  http.post('/api/v1/integrations/tunnel/device-login', () =>
    HttpResponse.json(
      successFor<typeof beginTunnelDeviceLogin>({
        verification_uri: 'https://github.com/login/device',
        user_code: 'MOCK-CODE',
        already_logged_in: false,
      }),
    ),
  ),
]

// ── Default test handlers (tunnel inactive). ──
export const tunnelDefaultHandlers = [
  http.get('/api/v1/integrations/tunnel/status', () =>
    HttpResponse.json(successFor<typeof getTunnelStatus>(snapshot())),
  ),
  http.post('/api/v1/integrations/tunnel/start', () =>
    HttpResponse.json(
      successFor<typeof startTunnel>({
        public_url: 'https://mock-tunnel.trycloudflare.com',
        provider: 'cloudflare',
      }),
    ),
  ),
  http.post('/api/v1/integrations/tunnel/stop', () => HttpResponse.json(voidSuccess())),
  http.put('/api/v1/integrations/tunnel/credential', () => HttpResponse.json(voidSuccess())),
  http.delete('/api/v1/integrations/tunnel/credential/:provider', () =>
    HttpResponse.json(voidSuccess()),
  ),
  http.post('/api/v1/integrations/tunnel/device-login', () =>
    HttpResponse.json(
      successFor<typeof beginTunnelDeviceLogin>({
        verification_uri: 'https://github.com/login/device',
        user_code: 'MOCK-CODE',
        already_logged_in: false,
      }),
    ),
  ),
]
