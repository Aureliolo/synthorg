import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it } from 'vitest'
import { listSessions } from '@/api/endpoints/auth'
import { useAuthStore } from '@/stores/auth'
import { useToastStore } from '@/stores/toast'
import { apiError, successFor, voidSuccess } from '@/mocks/handlers'
import { server } from '@/test-setup'
import type { SessionInfo } from '@/api/types/auth'

function buildSession(overrides: Partial<SessionInfo> = {}): SessionInfo {
  return {
    session_id: 'sess-1',
    user_id: 'user-1',
    username: 'admin',
    ip_address: '127.0.0.1',
    user_agent: 'Firefox',
    is_current: false,
    created_at: '2026-04-19T00:00:00Z',
    last_active_at: '2026-04-19T00:00:00Z',
    expires_at: '2026-04-20T00:00:00Z',
    ...overrides,
  }
}

describe('useAuthStore sessions', () => {
  beforeEach(() => {
    useAuthStore.setState({ sessions: [], sessionsLoading: false, sessionsError: null })
    useToastStore.getState().dismissAll()
  })

  it('fetches the active sessions', async () => {
    server.use(
      http.get('/api/v1/auth/sessions', () =>
        HttpResponse.json(successFor<typeof listSessions>([buildSession()])),
      ),
    )

    await useAuthStore.getState().fetchSessions('own')

    const state = useAuthStore.getState()
    expect(state.sessions).toHaveLength(1)
    expect(state.sessionsLoading).toBe(false)
  })

  it('records an error message when the session list call fails', async () => {
    server.use(
      http.get('/api/v1/auth/sessions', () =>
        HttpResponse.json(apiError('Network down'), { status: 500 }),
      ),
    )

    await useAuthStore.getState().fetchSessions('own')

    expect(typeof useAuthStore.getState().sessionsError).toBe('string')
    expect(useAuthStore.getState().sessionsLoading).toBe(false)
  })

  it('optimistically removes a session and keeps it removed on success', async () => {
    useAuthStore.setState({
      sessions: [buildSession({ session_id: 'a' }), buildSession({ session_id: 'b' })],
    })
    server.use(
      http.delete('/api/v1/auth/sessions/:id', () => HttpResponse.json(voidSuccess())),
    )

    const result = await useAuthStore.getState().revokeSession('a')

    expect(result).toBe(true)
    const state = useAuthStore.getState()
    expect(state.sessions.map((s) => s.session_id)).toEqual(['b'])
    expect(useToastStore.getState().toasts[0]!.variant).toBe('success')
    expect(useToastStore.getState().toasts[0]!.title).toBe('Session revoked')
  })

  it('rolls back and toasts an error on revoke failure', async () => {
    useAuthStore.setState({ sessions: [buildSession({ session_id: 'a' })] })
    server.use(
      http.delete('/api/v1/auth/sessions/:id', () =>
        HttpResponse.json(apiError('boom'), { status: 500 }),
      ),
    )

    const result = await useAuthStore.getState().revokeSession('a')

    expect(result).toBe(false)
    expect(useAuthStore.getState().sessions).toHaveLength(1)
    const toasts = useToastStore.getState().toasts
    expect(toasts[0]!.variant).toBe('error')
    expect(toasts[0]!.title).toBe('Failed to revoke session')
  })
})
