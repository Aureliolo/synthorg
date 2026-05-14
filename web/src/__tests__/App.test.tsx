import { render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { useAuthStore } from '@/stores/auth'
import { useSetupStore } from '@/stores/setup'
import { router } from '@/router'
import { successFor } from '@/mocks/handlers'
import type { getSetupStatus } from '@/api/endpoints/setup'
import { server } from '@/test-setup'
import App from '../App'

// Stub the global WebSocket subscription mounted in AppLayout -- it otherwise
// tries to open a real WS connection during these tests and triggers an
// EnvironmentTeardownError when the worker unmounts.
vi.mock('@/hooks/useGlobalNotifications', () => ({
  useGlobalNotifications: vi.fn(),
}))

// AppLayout + LoginPage are React.lazy() imports that transitively pull in
// motion, cmdk, Base UI, and every lazy route module. Under vitest with
// --coverage, that import chain can take 5-9s and race vitest's worker
// teardown. This test only asserts the router-guard routing behaviour,
// so we stub the lazy modules with minimal shells that render the
// landmarks the assertions check (nav + main, sign-in heading).
vi.mock('@/components/layout/AppLayout', () => ({
  default: () => (
    <>
      <nav aria-label="Main navigation">Nav</nav>
      <main>Content</main>
    </>
  ),
}))

vi.mock('@/pages/LoginPage', () => ({
  default: () => <h1>Sign in</h1>,
}))

// Prevent window.location side effects from auth store. Use a valid
// URL so the real axios client can resolve relative paths.
const originalLocation = window.location
beforeAll(() => {
  Object.defineProperty(window, 'location', {
    writable: true,
    value: {
      ...originalLocation,
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

describe('App', () => {
  beforeEach(() => {
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
    localStorage.clear()
    server.use(
      http.get('/api/v1/setup/status', () =>
        HttpResponse.json(
          successFor<typeof getSetupStatus>({
            needs_admin: false,
            needs_setup: false,
            has_providers: true,
            has_name_locales: true,
            has_company: true,
            has_agents: true,
            min_password_length: 12,
          }),
        ),
      ),
    )
  })

  it('redirects unauthenticated users to login', async () => {
    await router.navigate('/')
    render(<App />)
    await waitFor(() => {
      expect(
        screen.getByRole('heading', { name: /sign in/i }),
      ).toBeInTheDocument()
    })
  })

  it('renders app shell for authenticated users with setup complete', async () => {
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
    useSetupStore.setState({ setupComplete: true })
    await router.navigate('/')

    render(<App />)
    await waitFor(() => {
      expect(
        screen.getByRole('navigation', { name: /main navigation/i }),
      ).toBeInTheDocument()
    })
    expect(screen.getByRole('main')).toBeInTheDocument()
  })
})
