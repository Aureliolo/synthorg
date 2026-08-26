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
  MessagesSquare,
  Palette,
  Settings,
  ShieldCheck,
  Sparkles,
  Users,
} from 'lucide-react'
import { ROUTES } from '@/router/routes'
import { titleForPath } from '@/router/route-titles'
import { normalisedKey } from '@/utils/keyboard'
import { RouteBoundary } from '@/router/RouteBoundary'
import type { CommandItem } from '@/hooks/useCommandPalette'
import { useRegisterCommands } from '@/hooks/useCommandPalette'
import { useGlobalNotifications } from '@/hooks/useGlobalNotifications'
import { useNotificationsStore } from '@/stores/notifications'
import { useDashboardPrefs } from '@/stores/dashboard-prefs'
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
import { MobileUnsupportedOverlay } from '@/components/ui/mobile-unsupported'
import { SkeletonCard } from '@/components/ui/skeleton'
import { ToastContainer } from '@/components/ui/toast'
import { useRegisterShortcuts } from '@/hooks/use-shortcut-registry'
import { NotificationDrawer } from '@/components/notifications/NotificationDrawer'
import { Sidebar } from './Sidebar'
import { StatusBar } from './StatusBar'
import { WebResearchBanner } from './WebResearchBanner'

function PageLoadingFallback() {
  return (
    <div className="space-y-section-gap p-2" role="status">
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

function _isShortcutOriginEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  const tag = target.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true
  if (target.isContentEditable) return true
  return target.closest('[role="combobox"]') !== null
}

// Alt rather than Shift, matching its three siblings: a capital N is what any
// sentence beginning "No..." produces, so with focus anywhere but a field the
// operator's prose reached the shortcut handlers instead of the page. Three of
// the four global shortcuts already take a modifier prose cannot produce; this
// one was the exception. Alt, not Ctrl+Shift, because the browser owns that
// combination for a new private window.
function _isNotificationShortcut(e: KeyboardEvent): boolean {
  if (e.ctrlKey || e.metaKey) return false
  return e.altKey && normalisedKey(e) === 'n'
}

function useNotificationDrawerShortcuts(): {
  notificationDrawerOpen: boolean
  setNotificationDrawerOpen: React.Dispatch<React.SetStateAction<boolean>>
} {
  const [notificationDrawerOpen, setNotificationDrawerOpen] = useState(false)

  useEffect(() => {
    function handleToggle() {
      setNotificationDrawerOpen((prev) => !prev)
    }
    window.addEventListener('toggle-notification-drawer', handleToggle)
    return () => window.removeEventListener('toggle-notification-drawer', handleToggle)
  }, [])

  useEffect(() => {
    function handleOpen() {
      setNotificationDrawerOpen(true)
    }
    window.addEventListener('open-notification-drawer', handleOpen)
    return () => window.removeEventListener('open-notification-drawer', handleOpen)
  }, [])

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.defaultPrevented || e.repeat) return
      if (_isShortcutOriginEditable(e.target)) return
      if (!_isNotificationShortcut(e)) return
      e.preventDefault()
      window.dispatchEvent(new CustomEvent('toggle-notification-drawer'))
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [])

  return { notificationDrawerOpen, setNotificationDrawerOpen }
}

function useDocumentTitle(pathname: string): void {
  // Drive ``document.title`` from the active route so the browser tab and
  // the screen-reader page announcement always reflect the current page and
  // revert automatically on navigation away.
  useEffect(() => {
    document.title = titleForPath(pathname)
  }, [pathname])
}

function useNotificationNavigateBridge(): void {
  const navigate = useNavigate()
  useEffect(() => {
    function handleNav(e: Event) {
      const detail = (e as Event & { detail?: { href?: string } }).detail
      const href = detail?.href
      if (typeof href === 'string' && href.startsWith('/') && !href.startsWith('//')) {
        void navigate(href)
      }
    }
    window.addEventListener('notification-navigate', handleNav)
    return () => window.removeEventListener('notification-navigate', handleNav)
  }, [navigate])
}

interface ThemeMeta {
  PALETTE: Record<ColorPalette, { label: string; keywords: string[] }>
  DENSITY: Record<Density, { label: string; keywords: string[] }>
  TYPOGRAPHY: Record<Typography, { label: string }>
  ANIMATION: Record<AnimationPreset, { label: string; keywords: string[] }>
  SIDEBAR: Record<SidebarMode, { label: string; keywords: string[] }>
}

const THEME_META: ThemeMeta = {
  PALETTE: {
    'warm-ops': { label: 'Warm Ops', keywords: ['blue'] },
    'ice-station': { label: 'Ice Station', keywords: ['green', 'mint'] },
    stealth: { label: 'Stealth', keywords: ['purple', 'violet'] },
    signal: { label: 'Signal', keywords: ['orange', 'amber'] },
    neon: { label: 'Neon', keywords: ['cyan'] },
  },
  DENSITY: {
    dense: { label: 'Dense', keywords: ['compact', 'tight'] },
    balanced: { label: 'Balanced', keywords: ['default'] },
    medium: { label: 'Medium', keywords: [] },
    sparse: { label: 'Sparse', keywords: ['spacious'] },
  },
  TYPOGRAPHY: {
    geist: { label: 'Geist' },
    jetbrains: { label: 'JetBrains + Inter' },
    'ibm-plex': { label: 'IBM Plex' },
  },
  ANIMATION: {
    minimal: { label: 'Minimal', keywords: ['reduced'] },
    spring: { label: 'Spring', keywords: ['bouncy'] },
    instant: { label: 'Instant', keywords: ['none'] },
    'status-driven': { label: 'Status-driven', keywords: [] },
    aggressive: { label: 'Aggressive', keywords: ['energy'] },
  },
  SIDEBAR: {
    rail: { label: 'Rail', keywords: ['icons'] },
    collapsible: { label: 'Collapsible', keywords: ['default'] },
    hidden: { label: 'Hidden', keywords: ['full'] },
    persistent: { label: 'Persistent', keywords: ['always'] },
    compact: { label: 'Compact', keywords: ['narrow'] },
  },
}

function _buildThemeCommands(): CommandItem[] {
  return [
    { id: 'theme-open', label: 'Open theme preferences', icon: Palette, action: () => useThemeStore.getState().setPopoverOpen(true), group: 'Theme', keywords: ['theme', 'appearance', 'customise'] },
    ...COLOR_PALETTES.map((v) => ({ id: `theme-${v}`, label: `Theme: ${THEME_META.PALETTE[v].label}`, action: () => useThemeStore.getState().setColorPalette(v), group: 'Theme', keywords: ['color', 'palette', ...THEME_META.PALETTE[v].keywords] })),
    ...DENSITIES.map((v) => ({ id: `density-${v}`, label: `Set density: ${THEME_META.DENSITY[v].label}`, action: () => useThemeStore.getState().setDensity(v), group: 'Theme', keywords: ['density', ...THEME_META.DENSITY[v].keywords] })),
    ...TYPOGRAPHIES.map((v) => ({ id: `font-${v}`, label: `Font: ${THEME_META.TYPOGRAPHY[v].label}`, action: () => useThemeStore.getState().setTypography(v), group: 'Theme', keywords: ['typography', 'font'] })),
    ...ANIMATION_PRESETS.map((v) => ({ id: `animation-${v}`, label: `Motion: ${THEME_META.ANIMATION[v].label}`, action: () => useThemeStore.getState().setAnimation(v), group: 'Theme', keywords: ['animation', ...THEME_META.ANIMATION[v].keywords] })),
    ...SIDEBAR_MODES.map((v) => ({ id: `sidebar-${v}`, label: `Sidebar: ${THEME_META.SIDEBAR[v].label}`, action: () => useThemeStore.getState().setSidebarMode(v), group: 'Theme', keywords: ['sidebar', ...THEME_META.SIDEBAR[v].keywords] })),
  ]
}

function _buildGlobalNavCommands(
  navigate: (path: string) => void,
  navigateToDocs: () => void,
  openNotificationDrawer: () => void,
): CommandItem[] {
  return [
    { id: 'nav-dashboard', label: 'Dashboard', icon: LayoutDashboard, action: () => navigate(ROUTES.DASHBOARD), group: 'Navigation' },
    { id: 'nav-chat', label: 'Chat', icon: MessagesSquare, action: () => navigate(ROUTES.CHAT), group: 'Navigation', keywords: ['chat', 'chief of staff', 'talk', 'converse', 'charter', 'project'] },
    { id: 'nav-org', label: 'Org Chart', icon: GitBranch, action: () => navigate(ROUTES.ORG), group: 'Navigation' },
    { id: 'nav-tasks', label: 'Tasks', icon: KanbanSquare, action: () => navigate(ROUTES.TASKS), group: 'Navigation' },
    { id: 'nav-budget', label: 'Budget', icon: DollarSign, action: () => navigate(ROUTES.BUDGET), group: 'Navigation' },
    { id: 'nav-approvals', label: 'Approvals', icon: ShieldCheck, action: () => navigate(ROUTES.APPROVALS), group: 'Navigation' },
    { id: 'nav-agents', label: 'Agents', icon: Users, action: () => navigate(ROUTES.AGENTS), group: 'Navigation' },
    { id: 'nav-messages', label: 'Messages', icon: MessageSquare, action: () => navigate(ROUTES.MESSAGES), group: 'Navigation' },
    { id: 'nav-providers', label: 'Providers', icon: Cpu, action: () => navigate(ROUTES.PROVIDERS), group: 'Navigation' },
    { id: 'nav-meta', label: 'Meta Loop', icon: Sparkles, action: () => navigate(ROUTES.META), group: 'Navigation', keywords: ['meta', 'governance', 'self-improvement', 'loop'] },
    { id: 'nav-docs', label: 'Documentation', icon: BookOpen, action: navigateToDocs, group: 'Navigation', keywords: ['docs', 'help', 'guide', 'reference'] },
    { id: 'nav-settings', label: 'Settings', icon: Settings, action: () => navigate(ROUTES.SETTINGS), group: 'Navigation', shortcut: ['ctrl', ','] },
    { id: 'notifications-open', label: 'Notifications', icon: Bell, action: openNotificationDrawer, group: 'Navigation', shortcut: ['shift', 'N'] },
  ]
}

const GLOBAL_SHORTCUTS = [
  { keys: ['Ctrl', 'K'], label: 'Open command palette', group: 'Global' },
  { keys: ['?'], label: 'Show keyboard shortcuts', group: 'Global' },
  { keys: ['Alt', 'N'], label: 'Toggle notifications', group: 'Global' },
  { keys: ['Ctrl', ','], label: 'Open settings', group: 'Global' },
] as const

export interface AppShellBodyProps {
  /** Whether the nav column's width is known yet. */
  layoutReady: boolean
  pathname: string
  sidebarOverlayOpen: boolean
  onSidebarOverlayClose: () => void
}

/** The nav column and the content column beside it. */
export function AppShellBody({
  layoutReady,
  pathname,
  sidebarOverlayOpen,
  onSidebarOverlayClose,
}: AppShellBodyProps) {
  if (!layoutReady) {
    return (
      <div className="flex flex-1 overflow-hidden">
        <div className="flex-1 overflow-y-auto p-card">
          <PageLoadingFallback />
        </div>
      </div>
    )
  }
  return (
    <div className="flex flex-1 overflow-hidden">
      <Sidebar overlayOpen={sidebarOverlayOpen} onOverlayClose={onSidebarOverlayClose} />
      {/* Padding lives on the animated wrapper (which is ``h-full``), NOT on
          ``<main>``: an ``h-full`` child plus padding on the scroll parent
          overflows by the padding amount and yields scrollable dead space
          below the content. Inside the border-box ``h-full`` wrapper the
          padding is absorbed, and the Org Chart's concrete-height need (it
          relies on ``h-full``) is preserved. */}
      <main id="main-content" tabIndex={-1} aria-label="Main content" className="flex-1 overflow-y-auto">
        <RouteBoundary key={pathname}>
          <Suspense fallback={<PageLoadingFallback />}>
            <AnimatedPresence routeKey={pathname} className="p-card">
              <Outlet />
            </AnimatedPresence>
          </Suspense>
        </RouteBoundary>
      </main>
    </div>
  )
}

export default function AppLayout() {
  const location = useLocation()
  const navigate = useNavigate()
  const [sidebarOverlayOpen, setSidebarOverlayOpen] = useState(false)
  const { notificationDrawerOpen, setNotificationDrawerOpen } = useNotificationDrawerShortcuts()

  // Global WebSocket subscription for app-wide notifications so they
  // render regardless of the current page.
  useGlobalNotifications()
  useDocumentTitle(location.pathname)
  useNotificationNavigateBridge()

  // Hydrate backend-owned preferences (pure API consumer: no client-side
  // copies) once the authed shell mounts: appearance/theme axes and the
  // notification routing preferences. Failures degrade to the defaults already
  // applied at store init.
  useEffect(() => {
    void useThemeStore.getState().hydrate()
    void useNotificationsStore.getState().hydrate()
    void useDashboardPrefs.getState().hydrate()
  }, [])

  // Both of these decide the nav column's width, and both arrive from the
  // backend after the shell mounts. Painting the defaults and then correcting
  // them slides the content column sideways a beat into the session, under
  // whatever the operator is already reaching for, and a live run lost clicks
  // to irreversible controls that way. The column waits
  // until its width is known. The chrome above it does not depend on it, so
  // that paints at once. Both hydrations settle their flag even when the read
  // fails, so a backend that cannot answer falls through to the defaults
  // rather than holding the shell.
  const prefsHydrated = useDashboardPrefs((s) => s.hydrated)
  const appearanceHydrated = useThemeStore((s) => s.hydrated)
  const layoutReady = prefsHydrated && appearanceHydrated

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

  const navigatePath = useCallback((path: string) => {
    void navigate(path)
  }, [navigate])
  const globalCommands = useMemo(
    () => _buildGlobalNavCommands(navigatePath, navigateToDocs, openNotificationDrawer),
    [navigatePath, navigateToDocs, openNotificationDrawer],
  )
  useRegisterCommands(globalCommands)

  const themeCommands = useMemo(() => _buildThemeCommands(), [])
  useRegisterCommands(themeCommands)

  // Memoised so the registry's effect key (JSON.stringify(shortcuts))
  // is computed from a stable reference; otherwise every AppLayout
  // render re-registers the set.
  const globalShortcuts = useMemo(() => GLOBAL_SHORTCUTS.map((s) => ({ ...s, keys: [...s.keys] })), [])
  useRegisterShortcuts(globalShortcuts)

  // ``h-full`` (100% of the ``height:100%`` #root chain), NOT ``h-screen``
  // (100vh): 100vh ignores the scrollbar/chrome gutter, so the shell would
  // overrun the real viewport by that sliver and add a SECOND, document-level
  // scrollbar on top of the inner ``main`` scroll. h-full pins the shell to the
  // exact viewport so only ``main`` scrolls.
  return (
    <div className="flex h-full flex-col overflow-hidden bg-background">
      {/* Keyboard users can jump past the header + sidebar straight to content. */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:rounded-md focus:bg-card focus:px-3 focus:py-2 focus:text-sm focus:font-medium focus:text-foreground focus:shadow-[var(--so-shadow-card-hover)] focus:outline-none focus:ring-2 focus:ring-accent"
      >
        Skip to main content
      </a>
      <header>
        <StatusBar onHamburgerClick={openSidebarOverlay} sidebarOverlayOpen={sidebarOverlayOpen} />
        <WebResearchBanner />
      </header>
      <AppShellBody
        layoutReady={layoutReady}
        pathname={location.pathname}
        sidebarOverlayOpen={sidebarOverlayOpen}
        onSidebarOverlayClose={closeSidebarOverlay}
      />
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
