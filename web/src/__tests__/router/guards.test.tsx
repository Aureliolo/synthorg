import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { useAuthStore } from '@/stores/auth'
import { useSetupStore } from '@/stores/setup'
import {
  AuthGuard,
  GuestGuard,
  SetupCompleteGuard,
  SetupGuard,
} from '@/router/guards'
import { apiError, apiSuccess } from '@/mocks/handlers'
import { server } from '@/test-setup'
import { renderRoutes } from '../test-utils'

// Disable dev auth bypass so guards use the real auth flow.
vi.mock('@/utils/dev', () => ({ IS_DEV_AUTH_BYPASS: false }))

const completeStatus = {
  needs_admin: false,
  needs_setup: false,
  has_providers: true,
  has_name_locales: true,
  has_company: true,
  has_agents: true,
  min_password_length: 12,
}

const incompleteStatus = {
  needs_admin: false,
  needs_setup: true,
  has_providers: false,
  has_name_locales: false,
  has_company: false,
  has_agents: false,
  min_password_length: 12,
}

type SetupStatusMode =
  | { kind: 'success'; body: typeof completeStatus }
  | { kind: 'error' }

const setupStatusState: { mode: SetupStatusMode; calls: number } = {
  mode: { kind: 'success', body: completeStatus },
  calls: 0,
}

function installSetupHandler() {
  server.use(
    http.get('/api/v1/setup/status', () => {
      setupStatusState.calls += 1
      if (setupStatusState.mode.kind === 'error') {
        return HttpResponse.json(apiError('Network error'))
      }
      return HttpResponse.json(apiSuccess(setupStatusState.mode.body))
    }),
    http.get('/api/v1/auth/me', () =>
      HttpResponse.json(
        apiSuccess({
          id: '1',
          username: 'admin',
          role: 'ceo',
          must_change_password: false,
          org_roles: [],
          scoped_departments: [],
        }),
      ),
    ),
  )
}

const originalLocation = window.location
beforeAll(() => {
  Object.defineProperty(window, 'location', {
    writable: true,
    value: {
      ...(originalLocation as unknown as Record<string, unknown>),
      href: 'http://localhost/',
      origin: 'http://localhost',
      pathname: '/',
    },
  })
})
afterAll(() => {
  Object.defineProperty(window, 'location', {
    writable: true,
    value: originalLocation,
  })
})

function resetStores() {
  useAuthStore.setState({
    authStatus: 'unauthenticated',
    user: null,
    loading: false,
  })
  useSetupStore.setState({
    setupComplete: null,
    loading: false,
    error: false,
  })
}

describe('AuthGuard', () => {
  beforeEach(() => {
    resetStores()
    setupStatusState.mode = { kind: 'success', body: completeStatus }
    setupStatusState.calls = 0
    installSetupHandler()
  })

  it('redirects to /login when unauthenticated', () => {
    useAuthStore.setState({ authStatus: 'unauthenticated' })

    renderRoutes(
      [
        {
          path: '/',
          element: <AuthGuard />,
          children: [{ index: true, element: <div>Protected</div> }],
        },
        { path: '/login', element: <div>Login Page</div> },
      ],
      { initialEntries: ['/'] },
    )

    expect(screen.getByText('Login Page')).toBeInTheDocument()
    expect(screen.queryByText('Protected')).not.toBeInTheDocument()
  })

  it('renders children when authenticated', () => {
    useAuthStore.setState({
      authStatus: 'authenticated',
      user: {
        id: '1',
        username: 'admin',
        role: 'ceo',
        must_change_password: false,
        org_roles: [],
        scoped_departments: [],
      },
    })

    renderRoutes(
      [
        {
          path: '/',
          element: <AuthGuard />,
          children: [{ index: true, element: <div>Protected</div> }],
        },
        { path: '/login', element: <div>Login Page</div> },
      ],
      { initialEntries: ['/'] },
    )

    expect(screen.getByText('Protected')).toBeInTheDocument()
    expect(screen.queryByText('Login Page')).not.toBeInTheDocument()
  })

  it('shows loading while auth status is unknown (page refresh)', async () => {
    useAuthStore.setState({ authStatus: 'unknown' })

    renderRoutes(
      [
        {
          path: '/',
          element: <AuthGuard />,
          children: [{ index: true, element: <div>Protected</div> }],
        },
      ],
      { initialEntries: ['/'] },
    )

    expect(screen.getByText('Loading...')).toBeInTheDocument()
    expect(screen.queryByText('Protected')).not.toBeInTheDocument()

    await waitFor(() => {
      expect(screen.getByText('Protected')).toBeInTheDocument()
    })
  })
})

describe('SetupGuard', () => {
  beforeEach(() => {
    resetStores()
    setupStatusState.mode = { kind: 'success', body: completeStatus }
    setupStatusState.calls = 0
    installSetupHandler()
  })

  it('redirects to /setup when setup is not complete', () => {
    useAuthStore.setState({ authStatus: 'authenticated' })
    useSetupStore.setState({ setupComplete: false })

    renderRoutes(
      [
        {
          path: '/',
          element: <SetupGuard />,
          children: [{ index: true, element: <div>App Content</div> }],
        },
        { path: '/setup', element: <div>Setup Page</div> },
      ],
      { initialEntries: ['/'] },
    )

    expect(screen.getByText('Setup Page')).toBeInTheDocument()
    expect(screen.queryByText('App Content')).not.toBeInTheDocument()
  })

  it('renders children when setup is complete', () => {
    useAuthStore.setState({ authStatus: 'authenticated' })
    useSetupStore.setState({ setupComplete: true })

    renderRoutes(
      [
        {
          path: '/',
          element: <SetupGuard />,
          children: [{ index: true, element: <div>App Content</div> }],
        },
        { path: '/setup', element: <div>Setup Page</div> },
      ],
      { initialEntries: ['/'] },
    )

    expect(screen.getByText('App Content')).toBeInTheDocument()
  })

  it('shows loading state when setup status is unknown', () => {
    useAuthStore.setState({ authStatus: 'authenticated' })
    useSetupStore.setState({ setupComplete: null, loading: true })

    renderRoutes(
      [
        {
          path: '/',
          element: <SetupGuard />,
          children: [{ index: true, element: <div>App Content</div> }],
        },
      ],
      { initialEntries: ['/'] },
    )

    expect(screen.getByText('Loading...')).toBeInTheDocument()
  })

  it('triggers fetchSetupStatus when setup status is unknown', async () => {
    useAuthStore.setState({ authStatus: 'authenticated' })

    renderRoutes(
      [
        {
          path: '/',
          element: <SetupGuard />,
          children: [{ index: true, element: <div>App Content</div> }],
        },
        { path: '/setup', element: <div>Setup Page</div> },
      ],
      { initialEntries: ['/'] },
    )

    await waitFor(() => {
      expect(screen.getByText('App Content')).toBeInTheDocument()
    })
    expect(setupStatusState.calls).toBe(1)
  })

  it('shows error with retry when fetchSetupStatus fails', async () => {
    setupStatusState.mode = { kind: 'error' }
    useAuthStore.setState({ authStatus: 'authenticated' })

    renderRoutes(
      [
        {
          path: '/',
          element: <SetupGuard />,
          children: [{ index: true, element: <div>App Content</div> }],
        },
      ],
      { initialEntries: ['/'] },
    )

    await waitFor(() => {
      expect(
        screen.getByText('Failed to check setup status.'),
      ).toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument()

    setupStatusState.mode = { kind: 'success', body: completeStatus }

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: /retry/i }))

    await waitFor(() => {
      expect(screen.getByText('App Content')).toBeInTheDocument()
    })
    expect(setupStatusState.calls).toBeGreaterThanOrEqual(2)
  })
})

describe('GuestGuard', () => {
  beforeEach(() => {
    resetStores()
    installSetupHandler()
  })

  it('renders children when unauthenticated', () => {
    useAuthStore.setState({ authStatus: 'unauthenticated' })

    renderRoutes(
      [
        {
          path: '/login',
          element: (
            <GuestGuard>
              <div>Login Form</div>
            </GuestGuard>
          ),
        },
        { path: '/', element: <div>Dashboard</div> },
      ],
      { initialEntries: ['/login'] },
    )

    expect(screen.getByText('Login Form')).toBeInTheDocument()
  })

  it('redirects to / when already authenticated', () => {
    useAuthStore.setState({ authStatus: 'authenticated' })

    renderRoutes(
      [
        {
          path: '/login',
          element: (
            <GuestGuard>
              <div>Login Form</div>
            </GuestGuard>
          ),
        },
        { path: '/', element: <div>Dashboard</div> },
      ],
      { initialEntries: ['/login'] },
    )

    expect(screen.getByText('Dashboard')).toBeInTheDocument()
    expect(screen.queryByText('Login Form')).not.toBeInTheDocument()
  })

  it('shows loading when auth status is unknown', () => {
    useAuthStore.setState({ authStatus: 'unknown' })

    renderRoutes(
      [
        {
          path: '/login',
          element: (
            <GuestGuard>
              <div>Login Form</div>
            </GuestGuard>
          ),
        },
      ],
      { initialEntries: ['/login'] },
    )

    expect(screen.getByText('Loading...')).toBeInTheDocument()
    expect(screen.queryByText('Login Form')).not.toBeInTheDocument()
  })
})

describe('SetupCompleteGuard', () => {
  beforeEach(() => {
    resetStores()
    setupStatusState.mode = {
      kind: 'success',
      body: { ...incompleteStatus, needs_setup: true },
    }
    setupStatusState.calls = 0
    installSetupHandler()
  })

  it('renders children when not authenticated', () => {
    useAuthStore.setState({ authStatus: 'unauthenticated' })

    renderRoutes(
      [
        {
          path: '/setup',
          element: (
            <SetupCompleteGuard>
              <div>Setup Wizard</div>
            </SetupCompleteGuard>
          ),
        },
      ],
      { initialEntries: ['/setup'] },
    )

    expect(screen.getByText('Setup Wizard')).toBeInTheDocument()
  })

  it('redirects to / when authenticated and setup is complete', () => {
    useAuthStore.setState({ authStatus: 'authenticated' })
    useSetupStore.setState({ setupComplete: true })

    renderRoutes(
      [
        {
          path: '/setup',
          element: (
            <SetupCompleteGuard>
              <div>Setup Wizard</div>
            </SetupCompleteGuard>
          ),
        },
        { path: '/', element: <div>Dashboard</div> },
      ],
      { initialEntries: ['/setup'] },
    )

    expect(screen.getByText('Dashboard')).toBeInTheDocument()
    expect(screen.queryByText('Setup Wizard')).not.toBeInTheDocument()
  })

  it('renders children when authenticated but setup is not complete', () => {
    useAuthStore.setState({ authStatus: 'authenticated' })
    useSetupStore.setState({ setupComplete: false })

    renderRoutes(
      [
        {
          path: '/setup',
          element: (
            <SetupCompleteGuard>
              <div>Setup Wizard</div>
            </SetupCompleteGuard>
          ),
        },
        { path: '/', element: <div>Dashboard</div> },
      ],
      { initialEntries: ['/setup'] },
    )

    expect(screen.getByText('Setup Wizard')).toBeInTheDocument()
  })

  it('fetches setup status when authenticated and status unknown', async () => {
    useAuthStore.setState({ authStatus: 'authenticated' })

    renderRoutes(
      [
        {
          path: '/setup',
          element: (
            <SetupCompleteGuard>
              <div>Setup Wizard</div>
            </SetupCompleteGuard>
          ),
        },
        { path: '/', element: <div>Dashboard</div> },
      ],
      { initialEntries: ['/setup'] },
    )

    await waitFor(() => {
      expect(screen.getByText('Setup Wizard')).toBeInTheDocument()
    })
    expect(setupStatusState.calls).toBe(1)
  })

  it('redirects authenticated users to dashboard when fetch fails (fail-closed)', async () => {
    setupStatusState.mode = { kind: 'error' }
    useAuthStore.setState({ authStatus: 'authenticated' })

    renderRoutes(
      [
        {
          path: '/setup',
          element: (
            <SetupCompleteGuard>
              <div>Setup Wizard</div>
            </SetupCompleteGuard>
          ),
        },
        { path: '/', element: <div>Dashboard</div> },
      ],
      { initialEntries: ['/setup'] },
    )

    await waitFor(() => {
      expect(screen.getByText('Dashboard')).toBeInTheDocument()
    })
    expect(screen.queryByText('Setup Wizard')).not.toBeInTheDocument()
  })
})
