import { http, HttpResponse } from 'msw'
import { useTunnelStore } from '@/stores/tunnel'
import { server } from '@/test-setup'
import { apiError, apiSuccess, voidSuccess } from '@/mocks/handlers'

describe('useTunnelStore', () => {
  beforeEach(() => {
    useTunnelStore.getState().reset()
  })

  it('maps status.public_url to the running phase', async () => {
    server.use(
      http.get('/api/v1/integrations/tunnel/status', () =>
        HttpResponse.json(
          apiSuccess({
            public_url: 'https://abc.ngrok.io',
            has_auth_token: true,
          }),
        ),
      ),
    )
    await useTunnelStore.getState().fetchStatus()
    expect(useTunnelStore.getState().phase).toBe('on')
    expect(useTunnelStore.getState().publicUrl).toBe('https://abc.ngrok.io')
    expect(useTunnelStore.getState().hasAuthToken).toBe(true)
  })

  it('transitions to the stopped phase when no URL is returned', async () => {
    server.use(
      http.get('/api/v1/integrations/tunnel/status', () =>
        HttpResponse.json(
          apiSuccess({ public_url: null, has_auth_token: false }),
        ),
      ),
    )
    await useTunnelStore.getState().fetchStatus()
    expect(useTunnelStore.getState().phase).toBe('stopped')
    expect(useTunnelStore.getState().hasAuthToken).toBe(false)
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
        HttpResponse.json(apiSuccess({ public_url: 'https://new.ngrok.io' })),
      ),
    )
    await useTunnelStore.getState().start()
    expect(useTunnelStore.getState().phase).toBe('on')
    expect(useTunnelStore.getState().publicUrl).toBe('https://new.ngrok.io')
  })

  it('start moves to error phase on failure', async () => {
    server.use(
      http.post('/api/v1/integrations/tunnel/start', () =>
        HttpResponse.json(apiError('ngrok down')),
      ),
    )
    await useTunnelStore.getState().start()
    expect(useTunnelStore.getState().phase).toBe('error')
    expect(useTunnelStore.getState().error).toBe('ngrok down')
  })

  it('stop clears the URL on success', async () => {
    useTunnelStore.setState({ phase: 'on', publicUrl: 'https://abc.ngrok.io' })
    server.use(
      http.post('/api/v1/integrations/tunnel/stop', () =>
        HttpResponse.json(voidSuccess()),
      ),
    )
    await useTunnelStore.getState().stop()
    expect(useTunnelStore.getState().phase).toBe('stopped')
    expect(useTunnelStore.getState().publicUrl).toBeNull()
  })

  it('stop moves to error phase on failure', async () => {
    useTunnelStore.setState({ phase: 'on', publicUrl: 'https://abc.ngrok.io' })
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
})
