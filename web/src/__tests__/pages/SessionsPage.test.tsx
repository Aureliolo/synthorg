import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it } from 'vitest'
import SessionsPage from '@/pages/SessionsPage'
import { listSessions } from '@/api/endpoints/auth'
import { successFor } from '@/mocks/handlers'
import { server } from '@/test-setup'
import { useAuthStore } from '@/stores/auth'
import { useToastStore } from '@/stores/toast'
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

function seedSessions(sessions: SessionInfo[]) {
  server.use(
    http.get('/api/v1/auth/sessions', () =>
      HttpResponse.json(successFor<typeof listSessions>(sessions)),
    ),
  )
}

describe('SessionsPage', () => {
  beforeEach(() => {
    useAuthStore.setState({ sessions: [], sessionsLoading: false, sessionsError: null })
    useToastStore.getState().dismissAll()
  })

  it('marks the current device and only allows revoking other sessions', async () => {
    const user = userEvent.setup()
    seedSessions([
      buildSession({ session_id: 'current', user_agent: 'This Browser', is_current: true }),
      buildSession({ session_id: 'other', user_agent: 'Other Device', is_current: false }),
    ])
    render(
      <MemoryRouter>
        <SessionsPage />
      </MemoryRouter>,
    )

    await screen.findByText('Other Device')
    expect(screen.getByText('This device')).toBeInTheDocument()

    const revokeButtons = screen.getAllByRole('button', {
      name: /revoke session for/i,
    })
    expect(revokeButtons).toHaveLength(1)

    await user.click(revokeButtons[0]!)
    const dialog = await screen.findByRole('alertdialog')
    await user.click(within(dialog).getByRole('button', { name: /^revoke$/i }))

    await waitFor(() => {
      expect(
        useToastStore.getState().toasts.some((t) => t.title === 'Session revoked'),
      ).toBe(true)
    })
  })

  it('shows an empty state when there are no sessions', async () => {
    seedSessions([])
    render(
      <MemoryRouter>
        <SessionsPage />
      </MemoryRouter>,
    )

    expect(await screen.findByText('No active sessions')).toBeInTheDocument()
  })
})
