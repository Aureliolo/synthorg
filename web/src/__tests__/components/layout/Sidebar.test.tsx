import { act, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import fc from 'fast-check'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'
import { Sidebar } from '@/components/layout/Sidebar'
import { ROUTES } from '@/router/routes'
import { renderWithRouter } from '../../test-utils'

// Mock components defined at module level for ESLint compliance
function MockAnimatePresence({ children }: { children: React.ReactNode }) {
  return <>{children}</>
}

// React 19: ref is a regular prop, no forwardRef needed
function MockMotionDiv({ children, ref, ...allProps }: React.ComponentProps<'div'> & { ref?: React.Ref<HTMLDivElement> } & Record<string, unknown>) {
  const domProps = Object.fromEntries(
    Object.entries(allProps).filter(([key]) => !['variants', 'initial', 'animate', 'exit', 'transition'].includes(key)),
  ) as React.HTMLAttributes<HTMLDivElement>
  return <div ref={ref} {...domProps}>{children as React.ReactNode}</div>
}

vi.mock('motion/react', async () => {
  const actual = await vi.importActual<typeof import('motion/react')>('motion/react')
  return {
    ...actual,
    AnimatePresence: MockAnimatePresence,
    motion: new Proxy(actual.motion as object, {
      get(target, prop, receiver) {
        if (prop === 'div') return MockMotionDiv
        return Reflect.get(target, prop, receiver)
      },
    }) as typeof actual.motion,
  }
})

// Mock useBreakpoint so we can control breakpoint per-test
const getBreakpoint = vi.fn()
vi.mock('@/hooks/useBreakpoint', () => ({

  useBreakpoint: () => getBreakpoint(),
}))

// Prevent window.location side effects from auth store
const originalLocation = window.location
beforeAll(() => {
  Object.defineProperty(window, 'location', {
    writable: true,
    value: { ...originalLocation, href: '', pathname: '/' },
  })
})
afterAll(() => {
  Object.defineProperty(window, 'location', {
    writable: true,
    value: originalLocation,
  })
})

function resetStore() {
  useAuthStore.setState({
    authStatus: 'unauthenticated' as const,
    user: null,
    loading: false,
  })
}

function setup(initialEntries: string[] = ['/']) {
  useAuthStore.setState({
    authStatus: 'authenticated' as const,
    user: { id: '1', username: 'admin', role: 'ceo', must_change_password: false, org_roles: [], scoped_departments: [] },
    loading: false,
  })
  return renderWithRouter(<Sidebar />, { initialEntries })
}

describe('Sidebar', () => {
  beforeEach(() => {
    resetStore()
    useThemeStore.getState().setSidebarMode('collapsible')
    localStorage.clear()
    vi.clearAllMocks()
    getBreakpoint.mockReturnValue({
      breakpoint: 'desktop',
      isDesktop: true,
      isTablet: false,
      isMobile: false,
    })
  })

  it('renders all primary navigation items', () => {
    setup()

    expect(screen.getByText('Dashboard')).toBeInTheDocument()
    expect(screen.getByText('Org Chart')).toBeInTheDocument()
    expect(screen.getByText('Task Board')).toBeInTheDocument()
    expect(screen.getByText('Budget')).toBeInTheDocument()
    expect(screen.getByText('Approvals')).toBeInTheDocument()
  })

  it('renders all workspace navigation items', () => {
    setup()

    expect(screen.getByText('Agents')).toBeInTheDocument()
    expect(screen.getByText('Messages')).toBeInTheDocument()
    expect(screen.getByText('Meetings')).toBeInTheDocument()
    expect(screen.getByText('Providers')).toBeInTheDocument()
    expect(screen.getByText('Docs')).toBeInTheDocument()
    expect(screen.getByText('Settings')).toBeInTheDocument()
  })

  it('renders the Workspace section label', () => {
    setup()

    expect(screen.getByText('Workspace')).toBeInTheDocument()
  })

  it('renders user info when authenticated', () => {
    setup()

    expect(screen.getByText('admin')).toBeInTheDocument()
    expect(screen.getByText('ceo')).toBeInTheDocument()
  })

  it('collapses and persists state to localStorage', async () => {
    const user = userEvent.setup()
    setup()

    expect(screen.getByText('SynthOrg')).toBeInTheDocument()
    expect(localStorage.getItem('sidebar_collapsed')).toBeNull()

    await user.click(screen.getByTitle('Collapse sidebar'))

    expect(screen.queryByText('SynthOrg')).not.toBeInTheDocument()
    expect(localStorage.getItem('sidebar_collapsed')).toBe('true')
  })

  it('expands from collapsed state', async () => {
    localStorage.setItem('sidebar_collapsed', 'true')
    const user = userEvent.setup()
    setup()

    expect(screen.queryByText('SynthOrg')).not.toBeInTheDocument()

    await user.click(screen.getByTitle('Expand sidebar'))

    expect(screen.getByText('SynthOrg')).toBeInTheDocument()
    expect(localStorage.getItem('sidebar_collapsed')).toBe('false')
  })

  it('hides Workspace label when collapsed', () => {
    localStorage.setItem('sidebar_collapsed', 'true')
    setup()

    expect(screen.queryByText('Workspace')).not.toBeInTheDocument()
  })

  it('renders brand mark when collapsed', () => {
    localStorage.setItem('sidebar_collapsed', 'true')
    setup()

    expect(screen.getByText('S')).toBeInTheDocument()
  })

  it('calls logout when logout button is clicked', async () => {
    const user = userEvent.setup()
    const logoutSpy = vi.fn()
    useAuthStore.setState({
      ...useAuthStore.getState(),
      authStatus: 'authenticated',
      user: { id: '1', username: 'admin', role: 'ceo', must_change_password: false, org_roles: [], scoped_departments: [] },
      loading: false,
        logout: logoutSpy,
    })
    renderWithRouter(<Sidebar />, { initialEntries: ['/'] })

    await user.click(screen.getByTitle('Logout'))

    expect(logoutSpy).toHaveBeenCalledOnce()
  })

  describe('sidebarMode', () => {
    it('returns null when mode is hidden', () => {
      useThemeStore.getState().setSidebarMode('hidden')
      setup()

      expect(screen.queryByLabelText('Main navigation')).not.toBeInTheDocument()
    })

    it('is always collapsed in rail mode (no collapse toggle)', () => {
      useThemeStore.getState().setSidebarMode('rail')
      setup()

      // Collapsed state shows brand mark "S" instead of "SynthOrg"
      expect(screen.getByText('S')).toBeInTheDocument()
      expect(screen.queryByText('SynthOrg')).not.toBeInTheDocument()

      // Collapse toggle should not be present
      expect(screen.queryByTitle('Collapse sidebar')).not.toBeInTheDocument()
      expect(screen.queryByTitle('Expand sidebar')).not.toBeInTheDocument()
    })

    it('is always collapsed in compact mode (no collapse toggle)', () => {
      useThemeStore.getState().setSidebarMode('compact')
      setup()

      expect(screen.getByText('S')).toBeInTheDocument()
      expect(screen.queryByText('SynthOrg')).not.toBeInTheDocument()

      expect(screen.queryByTitle('Collapse sidebar')).not.toBeInTheDocument()
      expect(screen.queryByTitle('Expand sidebar')).not.toBeInTheDocument()
    })

    it('is always expanded in persistent mode (no collapse toggle)', () => {
      useThemeStore.getState().setSidebarMode('persistent')
      setup()

      expect(screen.getByText('SynthOrg')).toBeInTheDocument()
      expect(screen.queryByText('S')).not.toBeInTheDocument()

      expect(screen.queryByTitle('Collapse sidebar')).not.toBeInTheDocument()
      expect(screen.queryByTitle('Expand sidebar')).not.toBeInTheDocument()
    })

    it('shows collapse toggle only in collapsible mode', () => {
      useThemeStore.getState().setSidebarMode('collapsible')
      setup()

      expect(screen.getByTitle('Collapse sidebar')).toBeInTheDocument()
    })
  })

  it('returns null at mobile breakpoint', () => {
    getBreakpoint.mockReturnValue({
      breakpoint: 'mobile',
      isDesktop: false,
      isTablet: false,
      isMobile: true,
    })
    setup()
    expect(screen.queryByLabelText('Main navigation')).not.toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('forces collapsed at desktop-sm breakpoint regardless of user preference', () => {
    getBreakpoint.mockReturnValue({
      breakpoint: 'desktop-sm',
      isDesktop: true,
      isTablet: false,
      isMobile: false,
    })
    localStorage.setItem('sidebar_collapsed', 'false')
    setup()
    // Collapsed shows brand mark "S" instead of "SynthOrg"
    expect(screen.getByText('S')).toBeInTheDocument()
    expect(screen.queryByText('SynthOrg')).not.toBeInTheDocument()
  })

  describe('tablet overlay', () => {
    function setupTablet(overlayOpen: boolean, onOverlayClose = vi.fn()) {
      getBreakpoint.mockReturnValue({
        breakpoint: 'tablet',
        isDesktop: false,
        isTablet: true,
        isMobile: false,
      })
      useAuthStore.setState({
        authStatus: 'authenticated',
        user: { id: '1', username: 'admin', role: 'ceo', must_change_password: false, org_roles: [], scoped_departments: [] },
        loading: false,
          })
      return {
        onOverlayClose,
        ...renderWithRouter(
          <Sidebar overlayOpen={overlayOpen} onOverlayClose={onOverlayClose} />,
          { initialEntries: ['/'] },
        ),
      }
    }

    it('renders nothing when overlayOpen is false', () => {
      setupTablet(false)
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })

    it('renders dialog when overlayOpen is true', () => {
      setupTablet(true)
      expect(screen.getByRole('dialog')).toBeInTheDocument()
    })

    it('has aria-label "Navigation menu"', () => {
      setupTablet(true)
      expect(screen.getByRole('dialog')).toHaveAttribute('aria-label', 'Navigation menu')
    })

    it('shows SynthOrg branding', () => {
      setupTablet(true)
      expect(screen.getByText('SynthOrg')).toBeInTheDocument()
    })

    it('renders navigation items', () => {
      setupTablet(true)
      expect(screen.getByText('Dashboard')).toBeInTheDocument()
      expect(screen.getByText('Settings')).toBeInTheDocument()
    })

    it('does not call onOverlayClose on mount', () => {
      const { onOverlayClose } = setupTablet(true)
      expect(onOverlayClose).not.toHaveBeenCalled()
    })

    it('calls onOverlayClose when close button is clicked', async () => {
      const user = userEvent.setup()
      const { onOverlayClose } = setupTablet(true)
      await user.click(screen.getByLabelText('Close navigation menu'))
      expect(onOverlayClose).toHaveBeenCalledOnce()
    })

    it('calls onOverlayClose when Escape is pressed', async () => {
      const user = userEvent.setup()
      const { onOverlayClose } = setupTablet(true)
      await user.keyboard('{Escape}')
      expect(onOverlayClose).toHaveBeenCalledOnce()
    })

    it('calls onOverlayClose when overlay backdrop is clicked', async () => {
      const user = userEvent.setup()
      const { onOverlayClose } = setupTablet(true)
      await user.click(screen.getByTestId('drawer-overlay'))
      expect(onOverlayClose).toHaveBeenCalledOnce()
    })

    it('calls onOverlayClose on route navigation', async () => {
      const onOverlayClose = vi.fn()
      const { router } = setupTablet(true, onOverlayClose)
      await act(() => router.navigate('/settings'))
      expect(onOverlayClose).toHaveBeenCalledOnce()
    })

    // Each iteration mounts a tablet-sized Sidebar with a Base UI Drawer; the Drawer's
    // `useTransitionStatus` schedules requestAnimationFrame callbacks. The microtask-based
    // rAF shim in `test-setup.tsx` keeps these tests handle-free under the active-handle
    // gate (each frame is a microtask, draining within the test's await chain rather than
    // outliving unmount as a setTimeout(0) would).
    //
    // Coverage strategy: `it.each` enumerates every route exactly once (deterministic coverage,
    // stable CI timing), and a low-iteration `fast-check` property test shuffles ordering to
    // catch cross-render state leaks that a fixed order would miss.
    const staticRoutes = Object.values(ROUTES).filter((r) => !r.includes(':') && r !== '/')
    it.each(staticRoutes)(
      'close-on-navigate fires exactly once when navigating to %s',
      async (route) => {
        const onOverlayClose = vi.fn()
        const { router, unmount } = setupTablet(true, onOverlayClose)
        await act(() => router.navigate(route))
        expect(onOverlayClose).toHaveBeenCalledOnce()
        unmount()
      },
    )

    // Permutation coverage: each iteration mounts ONE Drawer and navigates
    // a random ordered subset of routes through it, so route-order
    // interactions on a shared component instance are actually exercised
    // (not masked by per-iteration remount). Cumulative call assertions
    // (toHaveBeenCalledTimes(index + 1)) verify every navigation fires
    // close-on-navigate rather than just the last one.
    //
    // The subset size is bounded (max 3) because each `navigate` call
    // schedules a setImmediate that only drains on unmount; sweeping
    // every route inside one mount would surface as a transient
    // tracked-Immediate at afterEach. 3 routes per iteration x 10 runs
    // gives 30 ordered-pair navigations per CI run, enough to exercise
    // cross-render state leaks without pressuring the active-handle
    // drain loop.
    const PERMUTATION_SIZE = Math.min(3, staticRoutes.length)
    it(
      'close-on-navigate is independent of route order (property)',
      { timeout: 15_000 },
      async () => {
        await fc.assert(
          fc.asyncProperty(
            fc.shuffledSubarray(staticRoutes, {
              minLength: PERMUTATION_SIZE,
              maxLength: PERMUTATION_SIZE,
            }),
            async (routesInRandomOrder) => {
              const onOverlayClose = vi.fn()
              const { router, unmount } = setupTablet(true, onOverlayClose)
              try {
                for (const [index, route] of routesInRandomOrder.entries()) {
                  await act(() => router.navigate(route))
                  expect(onOverlayClose).toHaveBeenCalledTimes(index + 1)
                }
              } finally {
                unmount()
              }
            },
          ),
          { numRuns: 10 },
        )
      },
    )
  })
})
