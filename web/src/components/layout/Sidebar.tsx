import { useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router'
import { X } from 'lucide-react'
import { createLogger } from '@/lib/logger'
import { cn } from '@/lib/utils'
import { useAuth } from '@/hooks/useAuth'
import { useBreakpoint } from '@/hooks/useBreakpoint'
import { useCommandPalette } from '@/hooks/useCommandPalette'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'
import { useWebSocketStore } from '@/stores/websocket'
import { Drawer } from '@/components/ui/drawer'
import { SidebarNav } from './SidebarNav'
import { SidebarFooter } from './SidebarFooter'
import { STORAGE_KEY, useCollapsedState } from './sidebar-storage'

export { STORAGE_KEY }

const log = createLogger('Sidebar')

const SIDEBAR_MODES_FORCING_COLLAPSED = new Set(['rail', 'compact'])

interface SidebarProps {
  /** Whether the overlay sidebar is visible (used at tablet breakpoints). */
  overlayOpen?: boolean
  /** Called when the overlay requests close. Required when overlayOpen is used. */
  onOverlayClose?: () => void
}

function _computeEffectiveCollapsed(
  breakpoint: string,
  sidebarMode: string,
  localCollapsed: boolean,
): boolean {
  if (breakpoint === 'desktop-sm') return true
  if (SIDEBAR_MODES_FORCING_COLLAPSED.has(sidebarMode)) return true
  if (sidebarMode === 'persistent') return false
  return localCollapsed
}

function _detectMacPlatform(): boolean {
  return typeof navigator !== 'undefined' && /Mac|iPod|iPhone|iPad/.test(navigator.platform)
}

function useIsMacPlatform(): boolean {
  // ``navigator.platform`` is a DOM global, so reading it inside the
  // render body trips ``@eslint-react/globals``. Defer the lookup to a
  // post-mount effect; the shortcut hint flips from the default
  // (``Ctrl``) to ``⌘`` once on hydration without ever touching
  // ``navigator`` during render.
  const [isMac, setIsMac] = useState(false)
  useEffect(() => {
    setIsMac(_detectMacPlatform())
  }, [])
  return isMac
}

function useNavigationOverlayClose(
  overlayOpen: boolean,
  onOverlayClose: (() => void) | undefined,
): void {
  const location = useLocation()
  const prevPathnameRef = useRef(location.pathname)
  useEffect(() => {
    if (prevPathnameRef.current === location.pathname) return
    prevPathnameRef.current = location.pathname
    if (overlayOpen && onOverlayClose) onOverlayClose()
    // Only trigger on route changes, not on prop changes
    // eslint-disable-next-line @eslint-react/exhaustive-deps
  }, [location.pathname])
}

type SidebarShape = 'hidden' | 'overlay' | 'desktop'

function _decideSidebarShape(
  breakpoint: string,
  sidebarMode: string,
  isHidden: boolean,
  isOverlayMode: boolean,
): SidebarShape {
  if (isHidden) return 'hidden'
  if (isOverlayMode) return 'overlay'
  if ((breakpoint === 'desktop' || breakpoint === 'desktop-sm') && sidebarMode === 'hidden') return 'hidden'
  return 'desktop'
}

function _noop(): void {}

function SidebarOverlay({
  open,
  onClose,
  toggleCollapse,
  openCommandPalette,
  shortcutKey,
  wsConnected,
  wsReconnectExhausted,
  user,
  logout,
}: {
  open: boolean
  onClose: () => void
  toggleCollapse: () => void
  openCommandPalette: () => void
  shortcutKey: string
  wsConnected: boolean
  wsReconnectExhausted: boolean
  user: { username: string; role: string } | null
  logout: () => void
}) {
  return (
    <Drawer
      open={open}
      onClose={onClose}
      side="left"
      ariaLabel="Navigation menu"
      className="w-60 min-w-60 max-w-60 bg-surface"
      contentClassName="flex h-full flex-col p-0"
    >
      <div className="flex h-14 shrink-0 items-center justify-between border-b border-border px-3">
        <span className="text-lg font-bold text-accent">SynthOrg</span>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close navigation menu"
          className={cn(
            'inline-flex size-8 items-center justify-center rounded-md text-muted-foreground transition-colors',
            'hover:bg-card-hover hover:text-foreground',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
          )}
        >
          <X className="size-5" aria-hidden="true" />
        </button>
      </div>
      <SidebarNav collapsed={false} />
      <SidebarFooter
        collapsed={false}
        showCollapseToggle={false}
        toggleCollapse={toggleCollapse}
        openCommandPalette={() => { onClose(); openCommandPalette() }}
        shortcutKey={shortcutKey}
        wsConnected={wsConnected}
        wsReconnectExhausted={wsReconnectExhausted}
        user={user}
        logout={logout}
      />
    </Drawer>
  )
}

function SidebarDesktop({
  collapsed,
  sidebarMode,
  showCollapseToggle,
  toggleCollapse,
  openCommandPalette,
  shortcutKey,
  wsConnected,
  wsReconnectExhausted,
  user,
  logout,
}: {
  collapsed: boolean
  sidebarMode: string
  showCollapseToggle: boolean
  toggleCollapse: () => void
  openCommandPalette: () => void
  shortcutKey: string
  wsConnected: boolean
  wsReconnectExhausted: boolean
  user: { username: string; role: string } | null
  logout: () => void
}) {
  const widthClass = sidebarMode === 'compact'
    ? 'w-[var(--so-sidebar-compact)]'
    : collapsed
      ? 'w-[var(--so-sidebar-collapsed)]'
      : 'w-[var(--so-sidebar-expanded)]'
  return (
    <aside
      className={cn(
        'flex h-full flex-col border-r border-border bg-surface transition-[width] duration-200',
        widthClass,
      )}
    >
      <div className="flex h-14 shrink-0 items-center border-b border-border px-3">
        {collapsed ? (
          <span className="mx-auto text-lg font-bold text-accent">S</span>
        ) : (
          <span className="text-lg font-bold text-accent">SynthOrg</span>
        )}
      </div>
      <SidebarNav collapsed={collapsed} />
      <SidebarFooter
        collapsed={collapsed}
        showCollapseToggle={showCollapseToggle}
        toggleCollapse={toggleCollapse}
        openCommandPalette={openCommandPalette}
        shortcutKey={shortcutKey}
        wsConnected={wsConnected}
        wsReconnectExhausted={wsReconnectExhausted}
        user={user}
        logout={logout}
      />
    </aside>
  )
}

export function Sidebar({ overlayOpen = false, onOverlayClose }: SidebarProps) {
  const [localCollapsed, setLocalCollapsed] = useCollapsedState()
  const sidebarMode = useThemeStore((s) => s.sidebarMode)
  const { user } = useAuth()
  const logout = useAuthStore((s) => s.logout)
  const { open: openCommandPalette } = useCommandPalette()
  const wsConnected = useWebSocketStore((s) => s.connected)
  const wsReconnectExhausted = useWebSocketStore((s) => s.reconnectExhausted)
  const { breakpoint } = useBreakpoint()
  const isMacPlatform = useIsMacPlatform()
  const shortcutKey = isMacPlatform ? '⌘' : 'Ctrl'

  useEffect(() => {
    if (process.env.NODE_ENV !== 'production' && overlayOpen && !onOverlayClose) {
      log.warn('`onOverlayClose` is required when `overlayOpen` is true; dismiss actions will be inert.')
    }
  }, [overlayOpen, onOverlayClose])

  useNavigationOverlayClose(overlayOpen, onOverlayClose)

  const isOverlayMode = breakpoint === 'tablet'
  const isHidden = breakpoint === 'mobile'
  const shape = _decideSidebarShape(breakpoint, sidebarMode, isHidden, isOverlayMode)
  const effectiveCollapsed = _computeEffectiveCollapsed(breakpoint, sidebarMode, localCollapsed)
  const collapsed = isOverlayMode ? false : effectiveCollapsed
  const showCollapseToggle = breakpoint === 'desktop' && sidebarMode === 'collapsible'

  function toggleCollapse() {
    setLocalCollapsed(!localCollapsed)
  }

  if (shape === 'hidden') return null

  const handleLogout = () => { void logout() }

  if (shape === 'overlay') {
    return (
      <SidebarOverlay
        open={overlayOpen}
        onClose={onOverlayClose ?? _noop}
        toggleCollapse={toggleCollapse}
        openCommandPalette={openCommandPalette}
        shortcutKey={shortcutKey}
        wsConnected={wsConnected}
        wsReconnectExhausted={wsReconnectExhausted}
        user={user}
        logout={handleLogout}
      />
    )
  }

  return (
    <SidebarDesktop
      collapsed={collapsed}
      sidebarMode={sidebarMode}
      showCollapseToggle={showCollapseToggle}
      toggleCollapse={toggleCollapse}
      openCommandPalette={openCommandPalette}
      shortcutKey={shortcutKey}
      wsConnected={wsConnected}
      wsReconnectExhausted={wsReconnectExhausted}
      user={user}
      logout={handleLogout}
    />
  )
}
