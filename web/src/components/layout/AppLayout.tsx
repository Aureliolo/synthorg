import { Suspense, useCallback, useEffect, useMemo, useState } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router'
import {
  Bell,
  BookOpen,
  Cpu,
  DollarSign,
  GitBranch,
  KanbanSquare,
  LayoutDashboard,
  MessageSquare,
  Palette,
  Settings,
  ShieldCheck,
  Users,
  Video,
} from 'lucide-react'
import { ROUTES } from '@/router/routes'
import type { CommandItem } from '@/hooks/useCommandPalette'
import { useRegisterCommands } from '@/hooks/useCommandPalette'
import { useGlobalNotifications } from '@/hooks/useGlobalNotifications'
import {
  useThemeStore,
  COLOR_PALETTES,
  DENSITIES,
  TYPOGRAPHIES,
  ANIMATION_PRESETS,
  SIDEBAR_MODES,
  type ColorPalette,
  type Density,
  type Typography,
  type AnimationPreset,
  type SidebarMode,
} from '@/stores/theme'
import { AnimatedPresence } from '@/components/ui/animated-presence'
import { CommandCheatsheet } from '@/components/ui/command-cheatsheet'
import { CommandPalette } from '@/components/ui/command-palette'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { MobileUnsupportedOverlay } from '@/components/ui/mobile-unsupported'
import { SkeletonCard } from '@/components/ui/skeleton'
import { ToastContainer } from '@/components/ui/toast'
import { useRegisterShortcuts } from '@/hooks/use-shortcut-registry'
import { NotificationDrawer } from '@/components/notifications/NotificationDrawer'
import { Sidebar } from './Sidebar'
import { StatusBar } from './StatusBar'

function PageLoadingFallback() {
  return (
    <div className="space-y-section-gap p-2" role="status" aria-live="polite">
      <SkeletonCard header lines={2} />
      <div className="grid grid-cols-4 gap-grid-gap">
        <SkeletonCard lines={1} />
        <SkeletonCard lines={1} />
        <SkeletonCard lines={1} />
        <SkeletonCard lines={1} />
      </div>
    </div>
  )
}

export default function AppLayout() {
  const location = useLocation()
  const navigate = useNavigate()
  const [sidebarOverlayOpen, setSidebarOverlayOpen] = useState(false)
  const [notificationDrawerOpen, setNotificationDrawerOpen] = useState(false)

  // Global WebSocket subscription for app-wide notifications (e.g. personality
  // trimming toasts) so they render regardless of the current page.
  useGlobalNotifications()

  // Listen for toggle-notification-drawer events (dispatched by Shift+N and
  // by the NotificationBell button). Handled here in AppLayout so the
  // listener stays mounted even when the sidebar is hidden at tablet.
  useEffect(() => {
    function handleToggle() {
      setNotificationDrawerOpen((prev) => !prev)
    }
    window.addEventListener('toggle-notification-drawer', handleToggle)
    return () => window.removeEventListener('toggle-notification-drawer', handleToggle)
  }, [])

  // Listen for open-notification-drawer events (one-directional open, used
  // by the bell button click which should always open, not toggle).
  useEffect(() => {
    function handleOpen() {
      setNotificationDrawerOpen(true)
    }
    window.addEventListener('open-notification-drawer', handleOpen)
    return () => window.removeEventListener('open-notification-drawer', handleOpen)
  }, [])

  // Shift+N toggles the notification drawer via the custom event above.
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.defaultPrevented || e.repeat) return
      const tag = (e.target as HTMLElement)?.tagName
      const el = e.target as HTMLElement
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el?.isContentEditable || el?.closest('[role="combobox"]')) return
      if (e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey && e.key === 'N') {
        e.preventDefault()
        window.dispatchEvent(new CustomEvent('toggle-notification-drawer'))
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [])

  // Click-to-focus from browser notifications navigates via React Router
  useEffect(() => {
    function handleNav(e: Event) {
      const detail = (e as CustomEvent<{ href: string }>).detail
      const href = detail?.href
      if (typeof href === 'string' && href.startsWith('/') && !href.startsWith('//')) {
        void navigate(href)
      }
    }
    window.addEventListener('notification-navigate', handleNav)
    return () => window.removeEventListener('notification-navigate', handleNav)
  }, [navigate])

  const openSidebarOverlay = useCallback(() => setSidebarOverlayOpen(true), [])
  const closeSidebarOverlay = useCallback(() => setSidebarOverlayOpen(false), [])

  // Hoist window-accessing handlers out of the useMemo body so the
  // ``react-x/globals`` rule sees them as event-handler bound (callable
  // outside render) rather than render-bound. /docs/ is static HTML
  // served by nginx, so it needs a full-page navigation rather than
  // ``navigate()``.
  const navigateToDocs = useCallback(() => {
    window.location.href = ROUTES.DOCUMENTATION
  }, [])
  const openNotificationDrawer = useCallback(() => {
    window.dispatchEvent(new CustomEvent('open-notification-drawer'))
  }, [])

  // Register global navigation commands for the command palette
  const globalCommands: CommandItem[] = useMemo(
    () => [
      { id: 'nav-dashboard', label: 'Dashboard', icon: LayoutDashboard, action: () => navigate(ROUTES.DASHBOARD), group: 'Navigation' },
      { id: 'nav-org', label: 'Org Chart', icon: GitBranch, action: () => navigate(ROUTES.ORG), group: 'Navigation' },
      { id: 'nav-tasks', label: 'Tasks', icon: KanbanSquare, action: () => navigate(ROUTES.TASKS), group: 'Navigation' },
      { id: 'nav-budget', label: 'Budget', icon: DollarSign, action: () => navigate(ROUTES.BUDGET), group: 'Navigation' },
      { id: 'nav-approvals', label: 'Approvals', icon: ShieldCheck, action: () => navigate(ROUTES.APPROVALS), group: 'Navigation' },
      { id: 'nav-agents', label: 'Agents', icon: Users, action: () => navigate(ROUTES.AGENTS), group: 'Navigation' },
      { id: 'nav-messages', label: 'Messages', icon: MessageSquare, action: () => navigate(ROUTES.MESSAGES), group: 'Navigation' },
      { id: 'nav-meetings', label: 'Meetings', icon: Video, action: () => navigate(ROUTES.MEETINGS), group: 'Navigation' },
      { id: 'nav-providers', label: 'Providers', icon: Cpu, action: () => navigate(ROUTES.PROVIDERS), group: 'Navigation' },
      { id: 'nav-docs', label: 'Documentation', icon: BookOpen, action: navigateToDocs, group: 'Navigation', keywords: ['docs', 'help', 'guide', 'reference'] },
      { id: 'nav-settings', label: 'Settings', icon: Settings, action: () => navigate(ROUTES.SETTINGS), group: 'Navigation', shortcut: ['ctrl', ','] },
      { id: 'notifications-open', label: 'Notifications', icon: Bell, action: openNotificationDrawer, group: 'Navigation', shortcut: ['shift', 'N'] },
    ],
    [navigate, navigateToDocs, openNotificationDrawer],
  )
  useRegisterCommands(globalCommands)

  const themeCommands: CommandItem[] = useMemo(() => {
    const PALETTE_META: Record<ColorPalette, { label: string; keywords: string[] }> = {
      'warm-ops': { label: 'Warm Ops', keywords: ['blue'] },
      'ice-station': { label: 'Ice Station', keywords: ['green', 'mint'] },
      stealth: { label: 'Stealth', keywords: ['purple', 'violet'] },
      signal: { label: 'Signal', keywords: ['orange', 'amber'] },
      neon: { label: 'Neon', keywords: ['cyan'] },
    }
    const DENSITY_META: Record<Density, { label: string; keywords: string[] }> = {
      dense: { label: 'Dense', keywords: ['compact', 'tight'] },
      balanced: { label: 'Balanced', keywords: ['default'] },
      medium: { label: 'Medium', keywords: [] },
      sparse: { label: 'Sparse', keywords: ['spacious'] },
    }
    const TYPOGRAPHY_META: Record<Typography, { label: string }> = {
      geist: { label: 'Geist' },
      jetbrains: { label: 'JetBrains + Inter' },
      'ibm-plex': { label: 'IBM Plex' },
    }
    const ANIMATION_META: Record<AnimationPreset, { label: string; keywords: string[] }> = {
      minimal: { label: 'Minimal', keywords: ['reduced'] },
      spring: { label: 'Spring', keywords: ['bouncy'] },
      instant: { label: 'Instant', keywords: ['none'] },
      'status-driven': { label: 'Status-driven', keywords: [] },
      aggressive: { label: 'Aggressive', keywords: ['energy'] },
    }
    const SIDEBAR_META: Record<SidebarMode, { label: string; keywords: string[] }> = {
      rail: { label: 'Rail', keywords: ['icons'] },
      collapsible: { label: 'Collapsible', keywords: ['default'] },
      hidden: { label: 'Hidden', keywords: ['full'] },
      persistent: { label: 'Persistent', keywords: ['always'] },
      compact: { label: 'Compact', keywords: ['narrow'] },
    }

    return [
      { id: 'theme-open', label: 'Open theme preferences', icon: Palette, action: () => useThemeStore.getState().setPopoverOpen(true), group: 'Theme', keywords: ['theme', 'appearance', 'customize'] },
      ...COLOR_PALETTES.map((v) => ({ id: `theme-${v}`, label: `Theme: ${PALETTE_META[v].label}`, action: () => useThemeStore.getState().setColorPalette(v), group: 'Theme', keywords: ['color', 'palette', ...PALETTE_META[v].keywords] })),
      ...DENSITIES.map((v) => ({ id: `density-${v}`, label: `Set density: ${DENSITY_META[v].label}`, action: () => useThemeStore.getState().setDensity(v), group: 'Theme', keywords: ['density', ...DENSITY_META[v].keywords] })),
      ...TYPOGRAPHIES.map((v) => ({ id: `font-${v}`, label: `Font: ${TYPOGRAPHY_META[v].label}`, action: () => useThemeStore.getState().setTypography(v), group: 'Theme', keywords: ['typography', 'font'] })),
      ...ANIMATION_PRESETS.map((v) => ({ id: `animation-${v}`, label: `Motion: ${ANIMATION_META[v].label}`, action: () => useThemeStore.getState().setAnimation(v), group: 'Theme', keywords: ['animation', ...ANIMATION_META[v].keywords] })),
      ...SIDEBAR_MODES.map((v) => ({ id: `sidebar-${v}`, label: `Sidebar: ${SIDEBAR_META[v].label}`, action: () => useThemeStore.getState().setSidebarMode(v), group: 'Theme', keywords: ['sidebar', ...SIDEBAR_META[v].keywords] })),
    ]
  }, [])
  useRegisterCommands(themeCommands)

  // Register global shortcuts surfaced by the ? cheatsheet. Memoised so the
  // registry's effect key (JSON.stringify(shortcuts)) is computed from a
  // stable reference; otherwise every AppLayout render re-registers the set.
  const globalShortcuts = useMemo(
    () => [
      { keys: ['Ctrl', 'K'], label: 'Open command palette', group: 'Global' },
      { keys: ['?'], label: 'Show keyboard shortcuts', group: 'Global' },
      { keys: ['Shift', 'N'], label: 'Toggle notifications', group: 'Global' },
      { keys: ['Ctrl', ','], label: 'Open settings', group: 'Global' },
    ],
    [],
  )
  useRegisterShortcuts(globalShortcuts)

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-background">
      <StatusBar onHamburgerClick={openSidebarOverlay} sidebarOverlayOpen={sidebarOverlayOpen} />
      <div className="flex flex-1 overflow-hidden">
        <Sidebar overlayOpen={sidebarOverlayOpen} onOverlayClose={closeSidebarOverlay} />
        <main className="flex-1 overflow-y-auto p-card">
          <ErrorBoundary level="page" onReset={() => { void navigate(ROUTES.DASHBOARD) }}>
            <Suspense fallback={<PageLoadingFallback />}>
              <AnimatedPresence routeKey={location.pathname}>
                <Outlet />
              </AnimatedPresence>
            </Suspense>
          </ErrorBoundary>
        </main>
      </div>
      <NotificationDrawer
        open={notificationDrawerOpen}
        onClose={() => setNotificationDrawerOpen(false)}
      />
      <ToastContainer />
      <CommandPalette />
      <CommandCheatsheet />
      <MobileUnsupportedOverlay />
    </div>
  )
}
