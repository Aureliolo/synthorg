import { Dialog } from '@base-ui/react/dialog'
import {
  Clock,
  Database,
  RefreshCw,
  Tag,
  Waves,
  Wifi,
  X,
  XCircle,
  type LucideIcon,
} from 'lucide-react'
import { useWebSocketStore } from '@/stores/websocket'
import { formatTime } from '@/utils/format'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { HealthStatusIcon } from './HealthStatusIcon'
import { HealthStatusRow } from './HealthStatusRow'
import {
  STATE_META,
  formatRelative,
  formatUptime,
  type LoadState,
  type SubsystemState,
} from './health-popover.utils'
import type { DerivedSubsystemStates } from './derive-subsystem-states'

const HERO_HEADLINES: Record<SubsystemState, string> = {
  ok: 'All systems operational',
  degraded: 'Some subsystems degraded',
  down: 'Backend unreachable',
  unknown: 'Status unknown',
  loading: 'Checking system health...',
}

const HERO_SUBS: Record<SubsystemState, string> = {
  ok: 'Every tracked component is reporting healthy.',
  degraded: 'One or more subsystems are not fully operational. Check the cards below.',
  down: 'The backend API is not responding. Live data may be stale.',
  unknown: 'No recent health snapshot. Waiting for the first probe to complete.',
  loading: 'Fetching the latest snapshot from the backend.',
}

function HealthPopoverHero({ state }: { state: SubsystemState }) {
  const meta = STATE_META[state]
  return (
    <div
      className={cn(
        'flex items-center gap-4 rounded-xl border p-card',
        meta.borderClass,
        meta.bgClass,
      )}
    >
      <HealthStatusIcon state={state} className="size-10" />
      <div className="flex-1">
        <div className={cn('text-lg font-semibold', meta.textClass)}>{HERO_HEADLINES[state]}</div>
        <p className="text-sm text-muted-foreground">{HERO_SUBS[state]}</p>
      </div>
    </div>
  )
}

function HealthMetadataRow({
  icon: Icon,
  label,
  value,
}: {
  icon: LucideIcon
  label: string
  value: string
}) {
  return (
    <div className="flex items-center gap-3">
      <Icon className="size-4 text-muted-foreground" aria-hidden={true} />
      <div className="flex flex-col">
        <span className="text-compact uppercase tracking-wider text-muted-foreground">
          {label}
        </span>
        <span className="text-sm font-medium text-foreground">{value}</span>
      </div>
    </div>
  )
}

function HealthSubsystemGrid({
  states,
}: {
  states: DerivedSubsystemStates
}) {
  const wsAction = states.wsState === 'down'
    ? {
        label: 'Retry now',
        onClick: () => {
          void useWebSocketStore.getState().retry()
        },
      }
    : undefined
  return (
    <div className="mt-4 grid grid-cols-1 gap-grid-gap sm:grid-cols-2">
      <HealthStatusRow
        icon={Wifi}
        label="Backend API"
        description="HTTP layer serving the dashboard, settings, and controller endpoints."
        state={states.apiState}
      />
      <HealthStatusRow
        icon={Wifi}
        label="Live stream (WebSocket)"
        description="Realtime push channel for agent activity, tasks, and notifications."
        state={states.wsState}
        detail={states.wsDetail}
        action={wsAction}
      />
      <HealthStatusRow
        icon={Database}
        label="Persistence"
        description="SQLite / configured persistence backend. Writes and queries roundtrip successfully."
        state={states.persistenceState}
      />
      <HealthStatusRow
        icon={Waves}
        label="Message bus"
        description="Internal async queue carrying inter-agent messages and engine events."
        state={states.busState}
      />
    </div>
  )
}

export interface HealthPopoverContentProps {
  loadState: LoadState
  states: DerivedSubsystemStates
  fetchedAtLabel: string | null
  onRefresh: () => void
}

export function HealthPopoverContent({
  loadState,
  states,
  fetchedAtLabel,
  onRefresh,
}: HealthPopoverContentProps) {
  const backendVersion = loadState.state === 'ok' ? loadState.data.version : '--'
  const uptime = loadState.state === 'ok'
    ? formatUptime(loadState.data.uptime_seconds)
    : '--'
  return (
    <>
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <Dialog.Title className="text-lg font-semibold text-foreground">
            System Health
          </Dialog.Title>
          <Dialog.Description className="text-compact text-muted-foreground">
            Live snapshot of the SynthOrg backend subsystems.
          </Dialog.Description>
        </div>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Refresh health snapshot"
            onClick={onRefresh}
            disabled={loadState.state === 'loading'}
            title="Refresh"
          >
            <RefreshCw
              className={cn(
                'size-4',
                loadState.state === 'loading' && 'animate-spin',
              )}
            />
          </Button>
          <Dialog.Close
            render={
              <Button variant="ghost" size="icon" type="button" aria-label="Close">
                <X className="size-4" />
              </Button>
            }
          />
        </div>
      </div>
      <HealthPopoverHero state={states.overallState} />
      {loadState.state === 'error' && (
        <div
          role="alert"
          className="mt-4 flex items-start gap-3 rounded-lg border border-danger/30 bg-danger/5 p-card text-sm text-danger"
        >
          <XCircle className="size-5 shrink-0" aria-hidden="true" />
          <div>
            <div className="font-semibold">Unable to reach the health endpoint</div>
            <div className="text-compact text-danger/80">{loadState.message}</div>
          </div>
        </div>
      )}
      <HealthSubsystemGrid states={states} />
      <div className="mt-6 grid grid-cols-1 gap-3 border-t border-border pt-4 sm:grid-cols-3">
        <HealthMetadataRow icon={Tag} label="Backend version" value={backendVersion} />
        <HealthMetadataRow icon={Clock} label="Uptime" value={uptime} />
        <HealthMetadataRow icon={RefreshCw} label="Last probed" value={fetchedAtLabel ?? '--'} />
      </div>
    </>
  )
}

export function buildFetchedAtLabel(
  loadState: LoadState,
  nowMs: number,
): string | null {
  if (loadState.state !== 'ok' && loadState.state !== 'error') return null
  const fetchedAt = loadState.fetchedAt
  return `${formatTime(fetchedAt.toISOString())} (${formatRelative(fetchedAt.getTime(), nowMs)})`
}
