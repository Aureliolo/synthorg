import { Dialog } from '@base-ui/react/dialog'
import { Link } from 'react-router'
import type { ReactNode } from 'react'
import {
  Archive,
  Brain,
  Clock,
  Database,
  Plug,
  RefreshCw,
  Tag,
  Waves,
  Wifi,
  X,
  XCircle,
  type LucideIcon,
} from 'lucide-react'
import { renderedSnapshot } from '@/stores/health'
import { useWebSocketStore } from '@/stores/websocket'
import { formatTime } from '@/utils/format'
import { cn } from '@/lib/utils'
import { ROUTES } from '@/router/routes'
import { Button } from '@/components/ui/button'
import { HealthStatusIcon } from './HealthStatusIcon'
import { HealthStatusRow } from './HealthStatusRow'
import type { LoadState } from '@/stores/health'
import {
  STATE_META,
  formatRelative,
  formatUptime,
  type SubsystemState,
} from './health-popover.utils'
import type { DerivedSubsystemStates } from './derive-subsystem-states'

const HERO_HEADLINES: Record<SubsystemState, string> = {
  ok: 'All systems operational',
  degraded: 'Some subsystems degraded',
  down: 'Some subsystems unreachable',
  unknown: 'Status unknown',
  loading: 'Checking system health...',
}

const HERO_SUBS: Record<SubsystemState, string> = {
  ok: 'Every tracked component is reporting healthy.',
  degraded: 'One or more subsystems are not fully operational. Check the cards below.',
  down: 'One or more subsystems are not responding. Check the cards below.',
  unknown: 'No recent health snapshot. Waiting for the first probe to complete.',
  loading: 'Fetching the latest snapshot from the backend.',
}

// Reserved for the one case it is actually true of. The roll-up reaches
// 'down' whenever any single subsystem is, so wording it as an API outage
// claimed the backend was unreachable while the cards beside it displayed
// data that backend had just returned.
const API_UNREACHABLE_HEADLINE = 'Backend unreachable'
const API_UNREACHABLE_SUB = 'The backend API is not responding. Live data may be stale.'

export interface HealthPopoverHeroProps {
  state: SubsystemState
  apiReachable: boolean
}

function HealthPopoverHero({ state, apiReachable }: HealthPopoverHeroProps) {
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
        <div className={cn('text-lg font-semibold', meta.textClass)}>
          {apiReachable ? HERO_HEADLINES[state] : API_UNREACHABLE_HEADLINE}
        </div>
        <p className="text-sm text-muted-foreground">
          {apiReachable ? HERO_SUBS[state] : API_UNREACHABLE_SUB}
        </p>
      </div>
    </div>
  )
}

export interface HealthMetadataRowProps {
  icon: LucideIcon
  label: string
  value: string
}

function HealthMetadataRow({
  icon: Icon,
  label,
  value,
}: HealthMetadataRowProps) {
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

const MEMORY_SETTINGS_PATH = ROUTES.SETTINGS_NAMESPACE.replace(':namespace', 'memory')

// The settings namespace page reads `?q=` as its filter, so a link carrying the
// key lands on the row that clears the fault rather than on the page holding it.
const EMBEDDER_SETTINGS_PATH = `${MEMORY_SETTINGS_PATH}?q=embedder_model`

export interface HealthRemediationLinkProps {
  /** Route that resolves the fault this card is reporting. */
  to: string
  label: string
  /** Closes the dialog, so it does not stay mounted over the destination. */
  onDismiss: () => void
}

/**
 * A card's route to the surface that fixes what it just diagnosed.
 *
 * A link rather than a button so it keeps what an operator expects of one: open
 * in a new tab, copy the address, and a destination announced as such. Dismissal
 * is an explicit `onClick` rather than a `Dialog.Close` wrapper because that
 * primitive is a button: it either warns that the rendered anchor is not one, or
 * silences the warning by stamping `role="button"` over the link semantics that
 * are the reason for using an anchor at all.
 */
function HealthRemediationLink({ to, label, onDismiss }: HealthRemediationLinkProps) {
  return (
    <Button variant="outline" size="sm" asChild>
      <Link to={to} onClick={onDismiss}>
        {label}
      </Link>
    </Button>
  )
}

/**
 * Whether a card should offer its remedy.
 *
 * `unknown` and `loading` are excluded because nothing is known to be wrong
 * yet, and routing an operator at a fix for a subsystem that has not reported
 * would send them to change a healthy setting.
 */
function needsAttention(state: SubsystemState): boolean {
  return state === 'degraded' || state === 'down'
}

function memoryRemediation(
  states: DerivedSubsystemStates,
  onDismiss: () => void,
): ReactNode {
  if (!needsAttention(states.memoryState)) return undefined
  // `off` is the one memory fault whose remedy is the operator's outright:
  // nothing is wired because no embedding model was ever named. Every other
  // unhealthy state is a wired backend misbehaving, where sending them to the
  // embedder row would point at a setting that is not the problem.
  return states.memoryBackendState === 'off' ? (
    <HealthRemediationLink
      to={EMBEDDER_SETTINGS_PATH}
      label="Choose an embedding model"
      onDismiss={onDismiss}
    />
  ) : (
    <HealthRemediationLink
      to={MEMORY_SETTINGS_PATH}
      label="Open memory settings"
      onDismiss={onDismiss}
    />
  )
}

export interface HealthSubsystemGridProps {
  states: DerivedSubsystemStates
  onDismiss: () => void
}

function HealthSubsystemGrid({
  states,
  onDismiss,
}: HealthSubsystemGridProps) {
  const wsAction = states.wsState === 'down'
    ? (
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => {
            void useWebSocketStore.getState().retry()
          }}
        >
          Retry now
        </Button>
      )
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
      <HealthStatusRow
        icon={Plug}
        label="Providers"
        description="Configured LLM providers reachable. An unreachable provider blocks readiness, so it needs somewhere to show."
        state={states.providersState}
        action={
          needsAttention(states.providersState) ? (
            <HealthRemediationLink
              to={ROUTES.PROVIDERS}
              label="Review providers"
              onDismiss={onDismiss}
            />
          ) : undefined
        }
      />
      <HealthStatusRow
        icon={Brain}
        label="Memory"
        description="Org, agent, and project recall injected into working agents. Durable requires an embedding model."
        state={states.memoryState}
        detail={states.memoryDetail}
        action={memoryRemediation(states, onDismiss)}
      />
      <HealthStatusRow
        icon={Archive}
        label="Backups"
        description="Scheduled snapshots of the database, memory, and config. Absent means no recovery point is being taken and every backup setting is inert."
        state={states.backupState}
        detail={states.backupDetail}
        action={
          needsAttention(states.backupState) ? (
            <HealthRemediationLink
              to={ROUTES.ADMIN_BACKUPS}
              label="Configure backups"
              onDismiss={onDismiss}
            />
          ) : undefined
        }
      />
    </div>
  )
}

export interface HealthPopoverContentProps {
  loadState: LoadState
  states: DerivedSubsystemStates
  fetchedAtLabel: string | null
  onRefresh: () => void
  /** Closes the dialog when a card's remedy is followed. */
  onDismiss: () => void
}

export function HealthPopoverContent({
  loadState,
  states,
  fetchedAtLabel,
  onRefresh,
  onDismiss,
}: HealthPopoverContentProps) {
  // Read through the rendered snapshot, like the cards do: reading `state ===
  // 'ok'` blanked both rows to `--` for the duration of every refresh while the
  // cards beside them kept displaying the very snapshot these describe.
  const snapshot = renderedSnapshot(loadState)
  const backendVersion = snapshot?.data.version ?? '--'
  const uptime = snapshot === null ? '--' : formatUptime(snapshot.data.uptime_seconds)
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
      <HealthPopoverHero
        state={states.withWebSocketState}
        apiReachable={states.apiState !== 'down'}
      />
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
      <HealthSubsystemGrid states={states} onDismiss={onDismiss} />
      <div className="mt-6 grid grid-cols-1 gap-grid-gap border-t border-border pt-4 sm:grid-cols-3">
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
  // A refresh keeps showing when the snapshot on screen was taken, rather than
  // blanking the label for the duration of the round trip: the cards are still
  // rendering that snapshot, so its age is exactly what the operator needs.
  const fetchedAt =
    loadState.state === 'error'
      ? loadState.fetchedAt
      : (renderedSnapshot(loadState)?.fetchedAt ?? null)
  if (fetchedAt === null) return null
  return `${formatTime(fetchedAt.toISOString())} (${formatRelative(fetchedAt.getTime(), nowMs)})`
}
