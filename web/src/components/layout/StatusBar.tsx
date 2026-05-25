import { useCallback, useEffect, useState } from 'react'
import { Menu } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useAnalyticsStore } from '@/stores/analytics'
import { useBreakpoint } from '@/hooks/useBreakpoint'
import { usePolling } from '@/hooks/usePolling'
import { getHealth } from '@/api/endpoints/health'
import { formatCurrency } from '@/utils/format'
import { HEALTH_POLL_INTERVAL } from '@/utils/constants'
import { LiveRegion } from '@/components/ui/live-region'
import { ThemeToggle } from '@/components/ui/theme-toggle'
import { HealthPopover } from '@/components/ui/health-popover'
import { useWebSocketStore } from '@/stores/websocket'
import type { HealthStatus } from '@/api/types/system'

type SystemStatus = 'unknown' | 'ok' | 'degraded' | 'down'

/**
 * Combine the HTTP health-probe status and the WebSocket connection
 * state into a single operator-facing pill.  Previously this was split
 * into two pills ("live" from the WS state and "all systems normal"
 * from the health probe) which were pure redundancy in the happy path
 * and only distinguished themselves when one signal was green and the
 * other was red.  The single pill applies a strict priority order so
 * the worst signal always wins, which is what an operator actually
 * wants to see.
 *
 * Priority (worst first):
 * 1. HTTP health reports ``down``      -> red    "system down"
 * 2. HTTP health reports ``degraded``  -> amber  "system degraded"
 * 3. HTTP health still ``unknown``     -> grey   "checking..." (initial
 *    mount -- do not escalate a disconnected WS to "reconnecting"
 *    before we have even confirmed the backend is up)
 * 4. WS reconnect budget exhausted     -> red    "live stream offline"
 * 5. WS still reconnecting             -> amber  "reconnecting"
 * 6. HTTP healthy AND WS connected     -> green  "all systems normal"
 *
 * **A11y contract**: callers MUST render the returned ``label`` as
 * visible text next to the colour-bearing ``Dot`` so high-contrast
 * and screen-reader users receive the status name. The colour-only
 * path is never sufficient. The health-button render below
 * satisfies this by pairing ``Dot`` with ``<span>{label}</span>``
 * and an outer ``aria-label`` on the wrapping button.
 */
function resolveCombinedStatus(
  healthStatus: SystemStatus,
  wsConnected: boolean,
  wsReconnectExhausted: boolean,
): { color: string; label: string } {
  if (healthStatus === 'down') {
    return { color: 'bg-danger', label: 'system down' }
  }
  if (healthStatus === 'degraded') {
    return { color: 'bg-warning', label: 'system degraded' }
  }
  if (healthStatus === 'unknown') {
    // If WebSocket reconnection is exhausted and we still have no
    // health response, the backend is likely unreachable -- show an
    // error state instead of "checking..." forever.
    if (wsReconnectExhausted) {
      return { color: 'bg-danger', label: 'unable to connect' }
    }
    return { color: 'bg-muted-foreground', label: 'checking...' }
  }
  if (wsReconnectExhausted) {
    return { color: 'bg-danger', label: 'live stream offline' }
  }
  if (!wsConnected) {
    return { color: 'bg-warning animate-pulse', label: 'reconnecting' }
  }
  return { color: 'bg-success', label: 'all systems normal' }
}

interface StatusBarProps {
  /** Callback to open the sidebar overlay (tablet mode). */
  onHamburgerClick?: () => void
  /** Whether the sidebar overlay is currently open. */
  sidebarOverlayOpen?: boolean
}

function AgentMetricsRow() {
  const totalAgents = useAnalyticsStore((s) => s.overview?.total_agents)
  const activeAgents = useAnalyticsStore((s) => s.overview?.active_agents_count)
  const idleAgents = useAnalyticsStore((s) => s.overview?.idle_agents_count)
  const totalTasks = useAnalyticsStore((s) => s.overview?.total_tasks)
  const dataLoaded = useAnalyticsStore((s) => s.overview !== null)
  return (
    <LiveRegion debounceMs={1500} politeness="polite" className="contents">
      <StatusItem>
        <Dot color="bg-accent" />
        <span>{dataLoaded ? `${totalAgents} agents` : '--'}</span>
      </StatusItem>
      <StatusItem>
        <Dot color="bg-success" />
        <span>{dataLoaded ? `${activeAgents} active` : '--'}</span>
      </StatusItem>
      <StatusItem>
        <Dot color="bg-muted-foreground" />
        <span>{dataLoaded ? `${idleAgents} idle` : '--'}</span>
      </StatusItem>
      <StatusItem>
        <Dot color="bg-warning" />
        <span>{dataLoaded ? `${totalTasks} tasks` : '--'}</span>
      </StatusItem>
    </LiveRegion>
  )
}

function BudgetSpendRow() {
  const totalCost = useAnalyticsStore((s) => s.overview?.total_cost)
  const currency = useAnalyticsStore((s) => s.overview?.currency)
  const budgetPercent = useAnalyticsStore((s) => s.overview?.budget_used_percent)
  const inReviewCount = useAnalyticsStore((s) => s.overview?.tasks_by_status?.in_review)
  const costDisplay = totalCost != null ? formatCurrency(totalCost, currency) : '--'
  const budgetDisplay = budgetPercent != null ? `${Math.round(budgetPercent)}%` : '--%'
  return (
    <>
      <StatusItem>
        <span className="text-muted-foreground">spend</span>
        <span className="ml-1.5 text-foreground">{costDisplay}</span>
        <span className="ml-1 text-muted-foreground">today</span>
      </StatusItem>
      <StatusItem>
        <span className="text-muted-foreground">budget</span>
        <span className="ml-1.5 text-foreground">{budgetDisplay}</span>
      </StatusItem>
      {inReviewCount != null && inReviewCount > 0 && (
        <StatusItem>
          <Dot color="bg-danger" />
          <span>{inReviewCount} in review</span>
        </StatusItem>
      )}
    </>
  )
}

function HealthStatusButton() {
  const wsConnected = useWebSocketStore((s) => s.connected)
  const wsReconnectExhausted = useWebSocketStore((s) => s.reconnectExhausted)
  const [healthStatus, setHealthStatus] = useState<SystemStatus>('unknown')

  // Poll system health. Readiness returns a binary outcome (``ok`` /
  // ``unavailable``); map ``unavailable`` to ``down`` so the StatusBar's
  // richer tri-state (unknown/ok/degraded/down) surfaces a failed
  // readiness probe as a hard-down signal rather than leaking through
  // as unknown.
  const pollHealth = useCallback(async () => {
    try {
      const health: HealthStatus = await getHealth()
      setHealthStatus(health.status === 'ok' ? 'ok' : 'down')
    } catch {
      // Preserve last known state on transient failures; only real
      // health payloads should set 'degraded' or 'down'.
    }
  }, [])

  const healthPolling = usePolling(pollHealth, HEALTH_POLL_INTERVAL)
  useEffect(() => {
    healthPolling.start()
    return () => healthPolling.stop()
    // healthPolling.start/stop are stable refs from usePolling.
    // eslint-disable-next-line @eslint-react/exhaustive-deps
  }, [])

  const statusCfg = resolveCombinedStatus(healthStatus, wsConnected, wsReconnectExhausted)
  return (
    <HealthPopover>
      <button
        type="button"
        aria-label={`System health: ${statusCfg.label}. Click for details.`}
        className="flex items-center whitespace-nowrap rounded px-1 -mx-1 hover:bg-card-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        <Dot color={statusCfg.color} />
        <span className="text-muted-foreground" aria-live="polite">{statusCfg.label}</span>
      </button>
    </HealthPopover>
  )
}

function HamburgerToggle({
  onClick,
  expanded,
}: {
  onClick: () => void
  expanded: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label="Open navigation menu"
      aria-expanded={expanded}
      className="flex items-center justify-center rounded-md p-0.5 text-muted-foreground hover:bg-card-hover hover:text-foreground"
    >
      <Menu className="size-4" aria-hidden="true" />
    </button>
  )
}

export function StatusBar({ onHamburgerClick, sidebarOverlayOpen = false }: StatusBarProps) {
  const { isTablet } = useBreakpoint()

  // Trigger overview fetch on mount if data isn't loaded yet.
  useEffect(() => {
    const state = useAnalyticsStore.getState()
    if (!state.overview && !state.loading) {
      void state.fetchOverview()
    }
  }, [])

  return (
    <div
      className={cn(
        'flex h-8 shrink-0 items-center gap-6',
        'border-b border-border bg-background px-6',
        'text-[11px] tracking-wide font-mono',
        'text-text-secondary select-none',
      )}
    >
      {isTablet && onHamburgerClick && (
        <HamburgerToggle onClick={onHamburgerClick} expanded={sidebarOverlayOpen} />
      )}
      <AgentMetricsRow />
      <Divider />
      <BudgetSpendRow />
      <div className="flex-1" />
      <HealthStatusButton />
      <Divider />
      <ThemeToggle />
    </div>
  )
}

function Divider() {
  return <span className="h-3 w-px shrink-0 bg-border" />
}

/**
 * Category-label dot for the StatusBar metric row (agents / active / idle /
 * tasks / in-review). Intentionally distinct from `<StatusBadge>`, which is
 * typed to `AgentRuntimeStatus` ('active'|'idle'|'error'|'offline') and owns
 * the agent-state color mapping. These dots are decorative labels, not state
 * indicators, so their colors (`bg-accent`, `bg-success`, `bg-muted-foreground`,
 * `bg-warning`, `bg-danger`) are driven by the metric category, not a status
 * value. Changing this to `<StatusBadge>` would require inventing synthetic
 * agent statuses that do not exist in the system.
 */
function Dot({ color }: { color: string }) {
  return (
    <span
      className={cn('mr-1.5 inline-block size-[5px] shrink-0 rounded-full', color)}
      aria-hidden="true"
    />
  )
}

function StatusItem({ children }: { children: React.ReactNode }) {
  return (
    <span className="flex items-center whitespace-nowrap">{children}</span>
  )
}
