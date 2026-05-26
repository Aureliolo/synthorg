import {
  Command,
  LogOut,
  PanelLeftClose,
  PanelLeftOpen,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { HealthPopover } from '@/components/ui/health-popover'
import { StatusBadge } from '@/components/ui/status-badge'
import { NotificationBell } from './NotificationBell'
import { SIDEBAR_BUTTON_CLASS } from './sidebar-button'

type WsUiState = 'connected' | 'disconnected' | 'reconnecting'

interface WsUiMeta {
  readonly label: string
  readonly text: string
  readonly badgeStatus: 'active' | 'error' | 'idle'
  readonly pulse: boolean
}

const WS_UI_META: Record<WsUiState, WsUiMeta> = {
  connected: { label: 'connected', text: 'Connected', badgeStatus: 'active', pulse: false },
  disconnected: { label: 'disconnected', text: 'Disconnected', badgeStatus: 'error', pulse: false },
  reconnecting: { label: 'reconnecting', text: 'Reconnecting...', badgeStatus: 'idle', pulse: true },
}

function _deriveWsUiState(wsConnected: boolean, wsReconnectExhausted: boolean): WsUiState {
  if (wsConnected) return 'connected'
  if (wsReconnectExhausted) return 'disconnected'
  return 'reconnecting'
}

function CollapseToggleButton({
  collapsed,
  onToggle,
}: {
  collapsed: boolean
  onToggle: () => void
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
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
  )
}

function CommandPaletteButton({
  collapsed,
  shortcutKey,
  onOpen,
}: {
  collapsed: boolean
  shortcutKey: string
  onOpen: () => void
}) {
  return (
    <button
      type="button"
      onClick={onOpen}
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
  )
}

function ConnectionStatusButton({
  collapsed,
  wsConnected,
  wsReconnectExhausted,
}: {
  collapsed: boolean
  wsConnected: boolean
  wsReconnectExhausted: boolean
}) {
  const ui = WS_UI_META[_deriveWsUiState(wsConnected, wsReconnectExhausted)]
  return (
    <HealthPopover>
      <button
        type="button"
        aria-label={`Connection status: ${ui.label}. Click for system health details.`}
        className={cn(
          'flex items-center gap-3 px-3 py-1 rounded-md',
          'transition-colors hover:bg-card-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
          collapsed && 'justify-center',
        )}
      >
        <StatusBadge status={ui.badgeStatus} pulse={ui.pulse} />
        {!collapsed && (
          <span className="text-xs text-muted-foreground">{ui.text}</span>
        )}
        <span className="sr-only" role="status" aria-live="polite">
          Connection status: {ui.label}
        </span>
      </button>
    </HealthPopover>
  )
}

function SidebarUserInfo({
  collapsed,
  user,
  onLogout,
}: {
  collapsed: boolean
  user: { username: string; role: string }
  onLogout: () => void
}) {
  return (
    <div
      className={cn(
        'flex items-center gap-3 px-3 py-2',
        collapsed && 'justify-center',
      )}
    >
      {!collapsed && (
        <div className="flex-1 truncate">
          <div className="text-sm font-medium text-foreground">{user.username}</div>
          <div className="text-xs text-muted-foreground">{user.role}</div>
        </div>
      )}
      <button
        type="button"
        onClick={onLogout}
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
  )
}

export interface SidebarFooterProps {
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

export function SidebarFooter({
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
          <CollapseToggleButton collapsed={collapsed} onToggle={toggleCollapse} />
        )}
        <NotificationBell collapsed={collapsed} />
        <CommandPaletteButton
          collapsed={collapsed}
          shortcutKey={shortcutKey}
          onOpen={openCommandPalette}
        />
        <ConnectionStatusButton
          collapsed={collapsed}
          wsConnected={wsConnected}
          wsReconnectExhausted={wsReconnectExhausted}
        />
        {user && (
          <SidebarUserInfo collapsed={collapsed} user={user} onLogout={logout} />
        )}
      </div>
    </div>
  )
}
