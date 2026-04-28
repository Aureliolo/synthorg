import { screen } from '@testing-library/react'
import * as fc from 'fast-check'
import { Users } from 'lucide-react'
import { SidebarNavItem } from '@/components/layout/SidebarNavItem'
import { router } from '@/router/index'
import { ROUTES } from '@/router/routes'
import type { RouteObject } from 'react-router'
import { renderWithRouter } from '../../test-utils'

describe('SidebarNavItem', () => {
  it('renders label and icon when expanded', () => {
    renderWithRouter(
      <SidebarNavItem to="/agents" icon={Users} label="Agents" collapsed={false} />,
    )

    expect(screen.getByText('Agents')).toBeInTheDocument()
  })

  it('hides label when collapsed and shows title tooltip', () => {
    renderWithRouter(
      <SidebarNavItem to="/agents" icon={Users} label="Agents" collapsed />,
    )

    expect(screen.queryByText('Agents')).not.toBeInTheDocument()
    expect(screen.getByTitle('Agents')).toBeInTheDocument()
  })

  it('renders badge when count is greater than 0', () => {
    renderWithRouter(
      <SidebarNavItem to="/approvals" icon={Users} label="Approvals" collapsed={false} badge={5} />,
    )

    expect(screen.getByText('5')).toBeInTheDocument()
  })

  it('caps badge at 99+', () => {
    renderWithRouter(
      <SidebarNavItem to="/approvals" icon={Users} label="Approvals" collapsed={false} badge={150} />,
    )

    expect(screen.getByText('99+')).toBeInTheDocument()
  })

  it('hides badge when count is 0', () => {
    renderWithRouter(
      <SidebarNavItem to="/approvals" icon={Users} label="Approvals" collapsed={false} badge={0} />,
    )

    expect(screen.queryByText('0')).not.toBeInTheDocument()
  })

  it('hides badge when collapsed', () => {
    renderWithRouter(
      <SidebarNavItem to="/approvals" icon={Users} label="Approvals" collapsed badge={5} />,
    )

    expect(screen.queryByText('5')).not.toBeInTheDocument()
  })

  it('renders dot indicator when dotColor is provided', () => {
    const { container } = renderWithRouter(
      <SidebarNavItem to="/agents" icon={Users} label="Agents" collapsed={false} dotColor="bg-success" />,
    )

    expect(screen.getByText('Agents')).toBeInTheDocument()
    const dot = container.querySelector('.bg-success.rounded-full')
    expect(dot).toBeInTheDocument()
  })

  it('does not render dot indicator when dotColor is not provided', () => {
    const { container } = renderWithRouter(
      <SidebarNavItem to="/agents" icon={Users} label="Agents" collapsed={false} />,
    )

    const dot = container.querySelector('.rounded-full.size-2')
    expect(dot).not.toBeInTheDocument()
  })

  it('caps badge display at 99+ for any count > 99 (property)', () => {
    fc.assert(
      fc.property(fc.integer({ min: 100, max: 10000 }), (count) => {
        const { unmount } = renderWithRouter(
          <SidebarNavItem to="/test" icon={Users} label="Test" collapsed={false} badge={count} />,
        )
        expect(screen.getByText('99+')).toBeInTheDocument()
        unmount()
      }),
    )
  })

  it('displays exact count for badge values 1-99 (property)', () => {
    fc.assert(
      fc.property(fc.integer({ min: 1, max: 99 }), (count) => {
        const { unmount } = renderWithRouter(
          <SidebarNavItem to="/test" icon={Users} label="Test" collapsed={false} badge={count} />,
        )
        expect(screen.getByText(String(count))).toBeInTheDocument()
        unmount()
      }),
    )
  })

  describe('external prop', () => {
    it('renders an anchor element instead of a router link', () => {
      renderWithRouter(
        <SidebarNavItem to="/docs/" icon={Users} label="Docs" collapsed={false} external />,
      )

      const link = screen.getByRole('link', { name: /docs/i })
      expect(link).toHaveAttribute('href', '/docs/')
      // External links open in a new tab and carry the security rel.
      expect(link).toHaveAttribute('target', '_blank')
      expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    })

    it('announces new-tab behavior to screen readers', () => {
      renderWithRouter(
        <SidebarNavItem to="/docs/" icon={Users} label="Docs" collapsed={false} external />,
      )

      expect(screen.getByText(/opens in new tab/i)).toBeInTheDocument()
    })

    it('shows title tooltip when collapsed', () => {
      renderWithRouter(
        <SidebarNavItem to="/docs/" icon={Users} label="Docs" collapsed external />,
      )

      expect(screen.getByTitle('Docs')).toBeInTheDocument()
      expect(screen.queryByText('Docs')).not.toBeInTheDocument()
    })

    it('renders label when expanded', () => {
      renderWithRouter(
        <SidebarNavItem to="/docs/" icon={Users} label="Docs" collapsed={false} external />,
      )

      expect(screen.getByText('Docs')).toBeInTheDocument()
    })

    it('renders badge on external link', () => {
      renderWithRouter(
        <SidebarNavItem to="/docs/" icon={Users} label="Docs" collapsed={false} badge={3} external />,
      )

      expect(screen.getByText('3')).toBeInTheDocument()
    })

    it('does not apply isActive styling', () => {
      const { container } = renderWithRouter(
        <SidebarNavItem to="/docs/" icon={Users} label="Docs" collapsed={false} external />,
        { initialEntries: ['/docs/'] },
      )

      const link = container.querySelector('a')
      // text-accent is only applied by NavLink's isActive callback -- external
      // anchors should never have it regardless of the current route
      expect(link?.className).not.toMatch(/text-accent/)
    })

    it('renders dot indicator when dotColor is provided', () => {
      const { container } = renderWithRouter(
        <SidebarNavItem to="/docs/" icon={Users} label="Docs" collapsed={false} dotColor="bg-success" external />,
      )

      const dot = container.querySelector('.bg-success.rounded-full')
      expect(dot).toBeInTheDocument()
    })

    it('does not pass end attribute to the anchor element', () => {
      const { container } = renderWithRouter(
        <SidebarNavItem to="/docs/" icon={Users} label="Docs" collapsed={false} end external />,
      )

      const link = container.querySelector('a')
      expect(link).not.toHaveAttribute('end')
    })
  })

  describe('inactivePaths prop', () => {
    it('forces the link inactive when current path is in inactivePaths', () => {
      const { container } = renderWithRouter(
        <SidebarNavItem
          to={ROUTES.SETTINGS}
          icon={Users}
          label="Settings"
          collapsed={false}
          inactivePaths={[ROUTES.SETTINGS_FINE_TUNING]}
        />,
        { initialEntries: [ROUTES.SETTINGS_FINE_TUNING] },
      )

      const link = container.querySelector('a')
      // text-accent is only applied when isActive && !forcedInactive.
      expect(link?.className).not.toMatch(/text-accent/)
    })

    it('forces inactive on a deeper child of an inactivePath entry', () => {
      const { container } = renderWithRouter(
        <SidebarNavItem
          to={ROUTES.SETTINGS}
          icon={Users}
          label="Settings"
          collapsed={false}
          inactivePaths={[ROUTES.SETTINGS_FINE_TUNING]}
        />,
        {
          initialEntries: [`${ROUTES.SETTINGS_FINE_TUNING}/checkpoints/abc`],
        },
      )

      const link = container.querySelector('a')
      expect(link?.className).not.toMatch(/text-accent/)
    })

    it('still highlights when the current path does not match any inactivePath', () => {
      const { container } = renderWithRouter(
        <SidebarNavItem
          to={ROUTES.SETTINGS}
          icon={Users}
          label="Settings"
          collapsed={false}
          inactivePaths={[ROUTES.SETTINGS_FINE_TUNING]}
        />,
        { initialEntries: [`${ROUTES.SETTINGS}/observability/sinks`] },
      )

      const link = container.querySelector('a')
      expect(link?.className).toMatch(/text-accent/)
    })

    it('does not affect highlighting when no inactivePaths is provided', () => {
      const { container } = renderWithRouter(
        <SidebarNavItem
          to={ROUTES.SETTINGS}
          icon={Users}
          label="Settings"
          collapsed={false}
        />,
        { initialEntries: [ROUTES.SETTINGS_FINE_TUNING] },
      )

      // Without inactivePaths, NavLink's prefix-match isActive applies.
      const link = container.querySelector('a')
      expect(link?.className).toMatch(/text-accent/)
    })

    it('match is by path-segment, not arbitrary substring', () => {
      // /settings-extras must NOT match /settings via prefix.
      const { container } = renderWithRouter(
        <SidebarNavItem
          to={ROUTES.SETTINGS}
          icon={Users}
          label="Settings"
          collapsed={false}
          inactivePaths={[ROUTES.SETTINGS_FINE_TUNING]}
        />,
        // Path that only string-prefix-matches the inactivePath entry
        // ("/settings/memory/fine-tuning-extras") must not falsely match;
        // the implementation checks `pathname === p || pathname.startsWith(p + '/')`.
        { initialEntries: [`${ROUTES.SETTINGS_FINE_TUNING}-extras`] },
      )

      const link = container.querySelector('a')
      // Highlight should remain (matches /settings prefix, not in inactivePaths).
      expect(link?.className).toMatch(/text-accent/)
    })
  })

  describe('DOCUMENTATION route invariants', () => {
    it('has a trailing slash (nginx location block requires it)', () => {
      expect(ROUTES.DOCUMENTATION).toBe('/docs/')
      expect(ROUTES.DOCUMENTATION.endsWith('/')).toBe(true)
    })

    it('is not registered as a React Router route (served by nginx)', () => {
      // Extract all path values from the actual router config recursively.
      // If someone adds a /docs/ route to router/index.tsx, this test fails.
      function extractPaths(routes: RouteObject[]): string[] {
        const paths: string[] = []
        for (const route of routes) {
          if (route.path) paths.push(route.path)
          if (route.children) paths.push(...extractPaths(route.children))
        }
        return paths
      }

      const allPaths = extractPaths(router.routes)
      expect(allPaths).not.toContain('/docs/')
      expect(allPaths).not.toContain('/docs')
      expect(allPaths).not.toContain('docs')
      expect(allPaths).not.toContain('docs/')
      for (const path of allPaths) {
        expect(path.startsWith('docs')).toBe(false)
      }
    })
  })
})
