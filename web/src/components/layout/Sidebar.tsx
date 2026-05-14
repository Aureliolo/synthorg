import { useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router'
import {
  Activity,
  Bell,
  BookOpen,
  Command,
  Cpu,
  DollarSign,
  FileText,
  GraduationCap,
  FolderKanban,
  GitBranch,
  Inbox,
  KanbanSquare,
  KeyRound,
  LayoutDashboard,
  Layers,
  LibraryBig,
  LogOut,
  MessageSquare,
  Package,
  PanelLeftClose,
  PanelLeftOpen,
  Plug,
  Scale,
  Settings,
  Shapes,
  ShieldCheck,
  Sparkles,
  UserCheck,
  Users,
  Video,
  Workflow,
  X,
} from 'lucide-react'
import { createLogger } from '@/lib/logger'
import { cn } from '@/lib/utils'
import { useAuth } from '@/hooks/useAuth'
import { useBreakpoint } from '@/hooks/useBreakpoint'
import { useCommandPalette } from '@/hooks/useCommandPalette'
import { useAuthStore } from '@/stores/auth'
import { useNotificationsStore } from '@/stores/notifications'
import { useThemeStore } from '@/stores/theme'
import { useWebSocketStore } from '@/stores/websocket'
import { ROUTES } from '@/router/routes'
import { Drawer } from '@/components/ui/drawer'
import { HealthPopover } from '@/components/ui/health-popover'
import { StatusBadge } from '@/components/ui/status-badge'
import { SidebarNavItem } from './SidebarNavItem'

const log = createLogger('Sidebar')

export const STORAGE_KEY = 'sidebar_collapsed'

const SIDEBAR_BUTTON_CLASS = cn(
  'flex items-center gap-3 rounded-md px-3 py-2 text-sm',
  'text-text-secondary transition-colors',
  'hover:bg-card-hover hover:text-foreground',
)

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'true'
  } catch {
    return false
  }
}

function writeCollapsed(value: boolean): void {
  try {
    localStorage.setItem(STORAGE_KEY, String(value))
  } catch {
    // Ignore -- storage may be unavailable (e.g. quota exceeded)
  }
}

interface SidebarProps {
  /** Whether the overlay sidebar is visible (used at tablet breakpoints). */
  overlayOpen?: boolean
  /** Called when the overlay requests close. Required when overlayOpen is used. */
  onOverlayClose?: () => void
}

export function Sidebar({ overlayOpen = false, onOverlayClose }: SidebarProps) {
  const [localCollapsed, setLocalCollapsed] = useState(readCollapsed)
  const sidebarMode = useThemeStore((s) => s.sidebarMode)
  const { user } = useAuth()
  const logout = useAuthStore((s) => s.logout)
  const { open: openCommandPalette } = useCommandPalette()
  const wsConnected = useWebSocketStore((s) => s.connected)
  const wsReconnectExhausted = useWebSocketStore((s) => s.reconnectExhausted)
  const { breakpoint } = useBreakpoint()
  const location = useLocation()

  const shortcutKey = typeof navigator !== 'undefined' && /Mac|iPod|iPhone|iPad/.test(navigator.platform) ? '⌘' : 'Ctrl'

  useEffect(() => {
    if (process.env.NODE_ENV !== 'production' && overlayOpen && !onOverlayClose) {
      log.warn('`onOverlayClose` is required when `overlayOpen` is true; dismiss actions will be inert.')
    }
  }, [overlayOpen, onOverlayClose])

  // Close overlay on navigation (skip the initial mount -- only fire on actual route changes)
  const prevPathnameRef = useRef(location.pathname)
  useEffect(() => {
    if (prevPathnameRef.current === location.pathname) return
    prevPathnameRef.current = location.pathname
    if (overlayOpen && onOverlayClose) {
      onOverlayClose()
    }
    // Only trigger on route changes, not on prop changes
    // eslint-disable-next-line @eslint-react/exhaustive-deps
  }, [location.pathname])

  // Compute effective sidebar state based on breakpoint
  // Do NOT mutate the theme store -- keep user preference intact
  const isOverlayMode = breakpoint === 'tablet'
  const isHidden = breakpoint === 'mobile'

  // At desktop-sm, force collapsed regardless of user preference
  const effectiveCollapsed =
    breakpoint === 'desktop-sm'
      ? true
      : sidebarMode === 'rail' || sidebarMode === 'compact'
        ? true
        : sidebarMode === 'persistent'
          ? false
          : localCollapsed

  const collapsed = isOverlayMode ? false : effectiveCollapsed
  const showCollapseToggle = breakpoint === 'desktop' && sidebarMode === 'collapsible'

  function toggleCollapse() {
    setLocalCollapsed((prev) => {
      const next = !prev
      writeCollapsed(next)
      return next
    })
  }

  // Hidden at mobile or when sidebarMode is 'hidden' at desktop
  if (isHidden) return null
  if ((breakpoint === 'desktop' || breakpoint === 'desktop-sm') && sidebarMode === 'hidden') return null

  // At tablet, render as overlay with backdrop (reuses shared Drawer component)
  if (isOverlayMode) {
    return (
      <Drawer
        open={overlayOpen}
        onClose={onOverlayClose ?? (() => {})}
        side="left"
        ariaLabel="Navigation menu"
        className="w-60 min-w-60 max-w-60 bg-surface"
        contentClassName="flex h-full flex-col p-0"
      >
        <div className="flex h-14 shrink-0 items-center justify-between border-b border-border px-3">
          <span className="text-lg font-bold text-accent">SynthOrg</span>
          <button
            type="button"
            onClick={onOverlayClose}
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
          openCommandPalette={() => { onOverlayClose?.(); openCommandPalette() }}
          shortcutKey={shortcutKey}
          wsConnected={wsConnected}
          wsReconnectExhausted={wsReconnectExhausted}
          user={user}
          logout={() => { void logout() }}
        />
      </Drawer>
    )
  }

  // Normal desktop sidebar
  return (
    <aside
      className={cn(
        'flex h-full flex-col border-r border-border bg-surface transition-[width] duration-200',
        sidebarMode === 'compact' ? 'w-[var(--so-sidebar-compact)]' : collapsed ? 'w-[var(--so-sidebar-collapsed)]' : 'w-[var(--so-sidebar-expanded)]',
      )}
    >
      {/* Header */}
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
        logout={() => { void logout() }}
      />
    </aside>
  )
}

// ── Extracted sub-components to share between normal and overlay modes ──

function SidebarNav({ collapsed }: { collapsed: boolean }) {
  return (
    <nav className="flex-1 overflow-y-auto px-2 py-3" aria-label="Main navigation">
      <div className="flex flex-col gap-1">
        <SidebarNavItem to={ROUTES.DASHBOARD} icon={LayoutDashboard} label="Dashboard" collapsed={collapsed} end />
        <SidebarNavItem to={ROUTES.ORG} icon={GitBranch} label="Org Chart" collapsed={collapsed} />
        <SidebarNavItem to={ROUTES.TASKS} icon={KanbanSquare} label="Task Board" collapsed={collapsed} />
        <SidebarNavItem to={ROUTES.BUDGET} icon={DollarSign} label="Budget" collapsed={collapsed} />
        <SidebarNavItem to={ROUTES.REPORTS} icon={FileText} label="Reports" collapsed={collapsed} />
        <SidebarNavItem to={ROUTES.APPROVALS} icon={ShieldCheck} label="Approvals" collapsed={collapsed} badge={0} />
        <SidebarNavItem to={ROUTES.SCALING} icon={Scale} label="Scaling" collapsed={collapsed} />
      </div>

      <div className="mt-4 border-t border-border pt-3">
        {!collapsed && (
          <span className="mb-2 block px-3 text-xs font-medium uppercase tracking-wider text-muted-foreground">
            Workspace
          </span>
        )}
        <div className="flex flex-col gap-1">
          <SidebarNavItem to={ROUTES.AGENTS} icon={Users} label="Agents" collapsed={collapsed} />
          <SidebarNavItem to={ROUTES.TRAINING} icon={GraduationCap} label="Training" collapsed={collapsed} />
          <SidebarNavItem to={ROUTES.PROJECTS} icon={FolderKanban} label="Projects" collapsed={collapsed} />
          <SidebarNavItem to={ROUTES.WORKFLOWS} icon={Workflow} label="Workflows" collapsed={collapsed} />
          <SidebarNavItem to={ROUTES.SUBWORKFLOWS} icon={Layers} label="Subworkflows" collapsed={collapsed} />
          <SidebarNavItem to={ROUTES.ARTIFACTS} icon={Package} label="Artifacts" collapsed={collapsed} />
          <SidebarNavItem to={ROUTES.MESSAGES} icon={MessageSquare} label="Messages" collapsed={collapsed} badge={0} />
          <SidebarNavItem to={ROUTES.MEETINGS} icon={Video} label="Meetings" collapsed={collapsed} />
          <SidebarNavItem to={ROUTES.PROVIDERS} icon={Cpu} label="Providers" collapsed={collapsed} />
          <SidebarNavItem to={ROUTES.ONTOLOGY} icon={Shapes} label="Ontology" collapsed={collapsed} />
        </div>
      </div>

      <div className="mt-4 border-t border-border pt-3">
        {!collapsed && (
          <span className="mb-2 block px-3 text-xs font-medium uppercase tracking-wider text-muted-foreground">
            Integrations
          </span>
        )}
        <div className="flex flex-col gap-1">
          <SidebarNavItem to={ROUTES.CONNECTIONS} icon={Plug} label="Connections" collapsed={collapsed} />
          <SidebarNavItem to={ROUTES.OAUTH_APPS} icon={KeyRound} label="OAuth Apps" collapsed={collapsed} />
          <SidebarNavItem to={ROUTES.MCP_CATALOG} icon={LibraryBig} label="MCP Catalog" collapsed={collapsed} />
        </div>
      </div>

      <div className="mt-4 border-t border-border pt-3">
        <div className="flex flex-col gap-1">
          <SidebarNavItem to={ROUTES.DOCUMENTATION} icon={BookOpen} label="Docs" collapsed={collapsed} external />
          <SidebarNavItem to={ROUTES.CLIENTS} icon={UserCheck} label="Clients" collapsed={collapsed} end />
          <SidebarNavItem to={ROUTES.REQUEST_QUEUE} icon={Inbox} label="Request Queue" collapsed={collapsed} />
          <SidebarNavItem to={ROUTES.SIMULATION_DASHBOARD} icon={Activity} label="Simulations" collapsed={collapsed} />
          <SidebarNavItem to={ROUTES.SETTINGS_FINE_TUNING} icon={Sparkles} label="Fine-Tuning" collapsed={collapsed} />
          <SidebarNavItem
            to={ROUTES.SETTINGS}
            icon={Settings}
            label="Settings"
            collapsed={collapsed}
            inactivePaths={[ROUTES.SETTINGS_FINE_TUNING]}
          />
        </div>
      </div>
    </nav>
  )
}

interface SidebarFooterProps {
  collapsed: boolean
  showCollapseToggle: boolean
  toggleCollapse: () => void
  openCommandPalette: () => void
  shortcutKey: string
  wsConnected: boolean
  wsReconnectExhausted: boolean
  user: { username: string; role: string } | null
  logout: () => void
}

function NotificationBell({ collapsed }: { collapsed: boolean }) {
  const unreadCount = useNotificationsStore((s) => s.unreadCount)

  return (
    <button
      type="button"
      title="Notifications (Shift+N)"
      aria-label={unreadCount > 0 ? `Notifications (${String(unreadCount)} unread)` : 'Notifications'}
      className={SIDEBAR_BUTTON_CLASS}
      onClick={() => window.dispatchEvent(new CustomEvent('open-notification-drawer'))}
    >
      <span className="relative">
        <Bell
          className={cn('size-5 shrink-0', collapsed && 'mx-auto')}
          aria-hidden="true"
        />
        {unreadCount > 0 && (
          <span
            className="absolute -right-1.5 -top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-danger px-1 text-[10px] font-semibold text-white"
            aria-hidden="true"
          >
            {unreadCount > 99 ? '99+' : String(unreadCount)}
          </span>
        )}
      </span>
      {!collapsed && (
        <span className="flex flex-1 items-center justify-between gap-2">
          <span>Notifications</span>
          {unreadCount > 0 && (
            <span className="text-xs text-muted-foreground" aria-live="polite">
              {String(unreadCount)}
            </span>
          )}
        </span>
      )}
    </button>
  )
}

function SidebarFooter({
  collapsed,
  showCollapseToggle,
  toggleCollapse,
  openCommandPalette,
  shortcutKey,
  wsConnected,
  wsReconnectExhausted,
  user,
  logout,
}: SidebarFooterProps) {
  return (
    <div className="border-t border-border px-2 py-3">
      <div className="flex flex-col gap-1">
        {showCollapseToggle && (
          <button
            type="button"
            onClick={toggleCollapse}
            title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            className={SIDEBAR_BUTTON_CLASS}
          >
            {collapsed ? (
              <PanelLeftOpen className="mx-auto size-5" aria-hidden="true" />
            ) : (
              <>
                <PanelLeftClose className="size-5 shrink-0" aria-hidden="true" />
                <span>Collapse</span>
              </>
            )}
          </button>
        )}

        <NotificationBell collapsed={collapsed} />

        <button
          type="button"
          onClick={openCommandPalette}
          title={`Search (${shortcutKey}+K)`}
          aria-label="Search commands"
          className={SIDEBAR_BUTTON_CLASS}
        >
          <Command
            className={cn('size-4 shrink-0', collapsed && 'mx-auto')}
            aria-hidden="true"
          />
          {!collapsed && (
            <span className="text-xs">
              {shortcutKey}+K to search
            </span>
          )}
        </button>

        {/* WebSocket connection status -- click to open the full health popover */}
        <HealthPopover>
          <button
            type="button"
            aria-label={
              wsConnected
                ? 'Connection status: connected. Click for system health details.'
                : wsReconnectExhausted
                  ? 'Connection status: disconnected. Click for system health details.'
                  : 'Connection status: reconnecting. Click for system health details.'
            }
            className={cn(
              'flex items-center gap-3 px-3 py-1 rounded-md',
              'transition-colors hover:bg-card-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
              collapsed && 'justify-center',
            )}
          >
            <StatusBadge
              status={wsConnected ? 'active' : wsReconnectExhausted ? 'error' : 'idle'}
              pulse={!wsConnected && !wsReconnectExhausted}
            />
            {!collapsed && (
              <span className="text-xs text-muted-foreground">
                {wsConnected
                  ? 'Connected'
                  : wsReconnectExhausted
                    ? 'Disconnected'
                    : 'Reconnecting...'}
              </span>
            )}
            {/* Screen reader live announcement for status changes */}
            <span className="sr-only" role="status" aria-live="polite">
              {wsConnected
                ? 'Connection status: connected'
                : wsReconnectExhausted
                  ? 'Connection status: disconnected'
                  : 'Connection status: reconnecting'}
            </span>
          </button>
        </HealthPopover>

        {user && (
          <div
            className={cn(
              'flex items-center gap-3 px-3 py-2',
              collapsed && 'justify-center',
            )}
          >
            {!collapsed && (
              <div className="flex-1 truncate">
                <div className="text-sm font-medium text-foreground">
                  {user.username}
                </div>
                <div className="text-xs text-muted-foreground">{user.role}</div>
              </div>
            )}
            <button
              type="button"
              onClick={logout}
              title="Logout"
              aria-label="Logout"
              className={cn(
                'rounded-md p-1 text-muted-foreground',
                'transition-colors',
                'hover:bg-card-hover hover:text-foreground',
              )}
            >
              <LogOut className="size-4" aria-hidden="true" />
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
