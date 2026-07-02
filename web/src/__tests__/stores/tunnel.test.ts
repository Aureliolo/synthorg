import { http, HttpResponse } from 'msw'
import { useTunnelStore } from '@/stores/tunnel'
import { server } from '@/test-setup'
import { apiError, apiSuccess, voidSuccess } from '@/mocks/handlers'
import { mockTunnelProviders } from '@/mocks/handlers/tunnel'

function statusPayload(overrides?: Record<string, unknown>) {
  return {
    public_url: null,
    selected_provider: 'cloudflare',
    active_provider: null,
    providers: mockTunnelProviders,
    ...overrides,
  }
}

describe('useTunnelStore', () => {
  beforeEach(() => {
    useTunnelStore.getState().reset()
  })

  it('maps status.public_url to the running phase', async () => {
    server.use(
      http.get('/api/v1/integrations/tunnel/status', () =>
        HttpResponse.json(
          apiSuccess(
            statusPayload({
              public_url: 'https://abc.trycloudflare.com',
              active_provider: 'cloudflare',
            }),
          ),
        ),
      ),
    )
    await useTunnelStore.getState().fetchStatus()
    const state = useTunnelStore.getState()
    expect(state.phase).toBe('on')
    expect(state.publicUrl).toBe('https://abc.trycloudflare.com')
    expect(state.activeProvider).toBe('cloudflare')
    expect(state.selectedProvider).toBe('cloudflare')
    expect(state.providers).toHaveLength(3)
  })

  it('transitions to the stopped phase when no URL is returned', async () => {
    server.use(
      http.get('/api/v1/integrations/tunnel/status', () =>
        HttpResponse.json(apiSuccess(statusPayload())),
      ),
    )
    await useTunnelStore.getState().fetchStatus()
    expect(useTunnelStore.getState().phase).toBe('stopped')
    expect(useTunnelStore.getState().activeProvider).toBeNull()
  })

  it('transitions to error phase when the status fetch fails', async () => {
    server.use(
      http.get('/api/v1/integrations/tunnel/status', () =>
        HttpResponse.json(apiError('fetch boom')),
      ),
    )
    await useTunnelStore.getState().fetchStatus()
    const state = useTunnelStore.getState()
    expect(state.phase).toBe('error')
    expect(state.error).toBe('fetch boom')
    expect(state.publicUrl).toBeNull()
  })

  it('start transitions enabling -> on on success', async () => {
    server.use(
      http.post('/api/v1/integrations/tunnel/start', () =>
        HttpResponse.json(
          apiSuccess({
            public_url: 'https://new.trycloudflare.com',
            provider: 'cloudflare',
          }),
        ),
      ),
    )
    await useTunnelStore.getState().start()
    const state = useTunnelStore.getState()
    expect(state.phase).toBe('on')
    expect(state.publicUrl).toBe('https://new.trycloudflare.com')
    expect(state.activeProvider).toBe('cloudflare')
  })

  it('start moves to error phase on failure', async () => {
    server.use(
      http.post('/api/v1/integrations/tunnel/start', () =>
        HttpResponse.json(apiError('provider down')),
      ),
    )
    await useTunnelStore.getState().start()
    expect(useTunnelStore.getState().phase).toBe('error')
    expect(useTunnelStore.getState().error).toBe('provider down')
  })

  it('stop clears the URL on success', async () => {
    useTunnelStore.setState({
      phase: 'on',
      publicUrl: 'https://abc.trycloudflare.com',
      activeProvider: 'cloudflare',
    })
    server.use(
      http.post('/api/v1/integrations/tunnel/stop', () =>
        HttpResponse.json(voidSuccess()),
      ),
    )
    await useTunnelStore.getState().stop()
    const state = useTunnelStore.getState()
    expect(state.phase).toBe('stopped')
    expect(state.publicUrl).toBeNull()
    expect(state.activeProvider).toBeNull()
  })

  it('stop moves to error phase on failure', async () => {
    useTunnelStore.setState({ phase: 'on', publicUrl: 'https://abc.trycloudflare.com' })
    server.use(
      http.post('/api/v1/integrations/tunnel/stop', () =>
        HttpResponse.json(apiError('tunnel stuck')),
      ),
    )
    await useTunnelStore.getState().stop()
    const state = useTunnelStore.getState()
    expect(state.phase).toBe('error')
    expect(state.error).toBe('tunnel stuck')
  })

  it('selectProvider writes the setting and keeps the optimistic value', async () => {
    useTunnelStore.setState({ selectedProvider: 'cloudflare' })
    let putBody: unknown = null
    server.use(
      http.put('/api/v1/settings/integrations/tunnel_provider', async ({ request }) => {
        putBody = await request.json()
        return HttpResponse.json(
          apiSuccess({
            namespace: 'integrations',
            key: 'tunnel_provider',
            value: 'ngrok',
            source: 'database',
          }),
        )
      }),
    )
    await useTunnelStore.getState().selectProvider('ngrok')
    expect(useTunnelStore.getState().selectedProvider).toBe('ngrok')
    expect(putBody).toEqual({ value: 'ngrok' })
  })

  it('selectProvider restores the previous value on failure', async () => {
    useTunnelStore.setState({ selectedProvider: 'cloudflare' })
    server.use(
      http.put('/api/v1/settings/integrations/tunnel_provider', () =>
        HttpResponse.json(apiError('nope')),
      ),
    )
    await useTunnelStore.getState().selectProvider('ngrok')
    expect(useTunnelStore.getState().selectedProvider).toBe('cloudflare')
  })

  it('saveCredential stores the token and refetches status', async () => {
    let putBody: unknown = null
    server.use(
      http.put('/api/v1/integrations/tunnel/credential', async ({ request }) => {
        putBody = await request.json()
        return HttpResponse.json(voidSuccess())
      }),
      http.get('/api/v1/integrations/tunnel/status', () =>
        HttpResponse.json(apiSuccess(statusPayload({ selected_provider: 'ngrok' }))),
      ),
    )
    const ok = await useTunnelStore.getState().saveCredential('ngrok', 'tok-123')
    expect(ok).toBe(true)
    expect(putBody).toEqual({ provider: 'ngrok', token: 'tok-123' })
    expect(useTunnelStore.getState().selectedProvider).toBe('ngrok')
  })

  it('saveCredential returns false on failure', async () => {
    server.use(
      http.put('/api/v1/integrations/tunnel/credential', () =>
        HttpResponse.json(apiError('no catalog')),
      ),
    )
    const ok = await useTunnelStore.getState().saveCredential('ngrok', 'tok-123')
    expect(ok).toBe(false)
    expect(useTunnelStore.getState().savingCredential).toBe(false)
  })

  it('beginDeviceLogin stores the prompt', async () => {
    server.use(
      http.post('/api/v1/integrations/tunnel/device-login', () =>
        HttpResponse.json(
          apiSuccess({
            verification_uri: 'https://github.com/login/device',
            user_code: 'ABCD-1234',
            already_logged_in: false,
          }),
        ),
      ),
    )
    await useTunnelStore.getState().beginDeviceLogin('devtunnels')
    expect(useTunnelStore.getState().deviceLogin?.user_code).toBe('ABCD-1234')
  })
})
